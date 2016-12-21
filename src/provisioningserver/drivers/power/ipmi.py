# Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""IPMI Power Driver."""

__all__ = []

import re
from subprocess import (
    PIPE,
    Popen,
)
from tempfile import NamedTemporaryFile

from provisioningserver.drivers.power import (
    is_power_parameter_set,
    PowerAuthError,
    PowerConnError,
    PowerDriver,
    PowerError,
    PowerFatalError,
    PowerSettingError,
)
from provisioningserver.logger import get_maas_logger
from provisioningserver.utils import shell
from provisioningserver.utils.network import find_ip_via_arp


IPMI_CONFIG = """\
Section Chassis_Boot_Flags
        Boot_Flags_Persistent                         No
        Boot_Device                                   PXE
EndSection
"""


IPMI_ERRORS = {
    'username invalid': {
        'message': (
            "Incorrect username.  Check BMC configuration and try again."),
        'exception': PowerAuthError
    },
    'password invalid': {
        'message': (
            "Incorrect password.  Check BMC configuration and try again."),
        'exception': PowerAuthError
    },
    'password verification timeout': {
        'message': (
            "Authentication timeout.  Check BMC configuration and try again."),
        'exception': PowerAuthError
    },
    'k_g invalid': {
        'message': (
            "Incorrect K_g key.  Check BMC configuration and try again."),
        'exception': PowerAuthError
    },
    'privilege level insufficient': {
        'message': (
            "Access denied while performing power action."
            "  Check BMC configuration and try again."),
        'exception': PowerAuthError
    },
    'privilege level cannot be obtained for this user': {
        'message': (
            "Access denied while performing power action."
            "  Check BMC configuration and try again."),
        'exception': PowerAuthError
    },
    'authentication type unavailable for attempted privilege level': {
        'message': (
            "Access denied while performing power action."
            "  Check BMC configuration and try again."),
        'exception': PowerSettingError
    },
    'cipher suite id unavailable': {
        'message': (
            "Access denied while performing power action: cipher suite"
            " unavailable.  Check BMC configuration and try again."),
        'exception': PowerSettingError
    },
    'ipmi 2.0 unavailable': {
        'message': (
            "IPMI 2.0 was not discovered on the BMC."
            "  Please try to use IPMI 1.5 instead."),
        'exception': PowerSettingError
    },
    'connection timeout': {
        'message': (
            "Connection timed out while performing power action."
            "  Check BMC configuration and connectivity and try again."),
        'exception': PowerConnError
    },
    'session timeout': {
        'message': (
            "The IPMI session has timed out. MAAS performed several retries."
            "  Check BMC configuration and connectivity and try again."),
        'exception': PowerConnError
    },
    'internal IPMI error': {
        'message': (
            "An IPMI error has occurred that FreeIPMI does not know how to"
            " handle.  Please try the power action manually, and file a bug if"
            " appropriate."),
        'exception': PowerFatalError
    },
    'device not found': {
        'message': (
            "Error locating IPMI device."
            "  Check BMC configuration and try again."),
        'exception': PowerSettingError
    },
    'driver timeout': {
        'message': (
            "Device communication timeout while performing power action."
            "  MAAS performed several retries.  Check BMC configuration and"
            " connectivity and try again."),
        'exception': PowerConnError
    },
    'message timeout': {
        'message': (
            "Device communication timeout while performing power action."
            "  MAAS performed several retries.  Check BMC configuration and"
            " connectivity and try again."),
        'exception': PowerConnError
    },
    'BMC busy': {
        'message': (
            "Device busy while performing power action."
            "  MAAS performed several retries.  Please wait and try again."),
        'exception': PowerConnError
    },
    'could not find inband device': {
        'message': (
            "An inband device could not be found."
            "  Check BMC configuration and try again."),
        'exception': PowerSettingError
    },
}


maaslog = get_maas_logger("drivers.power.ipmi")


class IPMIPowerDriver(PowerDriver):

    name = 'ipmi'
    description = "IPMI Power Driver."
    settings = []
    wait_time = (4, 8, 12)

    def detect_missing_packages(self):
        if not shell.has_command_available('ipmipower'):
            return ['freeipmi-tools']
        return []

    @staticmethod
    def _issue_ipmi_chassis_config_command(
            command, power_change, power_address):
        env = shell.select_c_utf8_locale()
        with NamedTemporaryFile("w+", encoding="utf-8") as tmp_config:
            # Write out the chassis configuration.
            tmp_config.write(IPMI_CONFIG)
            tmp_config.flush()
            # Use it when running the chassis config command.
            # XXX: Not using call_and_check here because we
            # need to check stderr.
            command = tuple(command) + ("--filename", tmp_config.name)
            process = Popen(command, stdout=PIPE, stderr=PIPE, env=env)
            _, stderr = process.communicate()
        stderr = stderr.decode("utf-8").strip()
        # XXX newell 2016-11-21 bug=1516065: Some IPMI hardware have timeout
        # issues when trying to set the boot order to PXE.  We want to
        # continue and not raise an error here.
        ipmi_errors = {
            key: IPMI_ERRORS[key] for key in IPMI_ERRORS
            if IPMI_ERRORS[key]['exception'] == PowerAuthError
        }
        for error, error_info in ipmi_errors.items():
            if error in stderr:
                raise error_info.get('exception')(error_info.get('message'))
        if process.returncode != 0:
            maaslog.warning(
                "Failed to change the boot order to PXE %s: %s" % (
                    power_address, stderr))

    @staticmethod
    def _issue_ipmipower_command(command, power_change, power_address):
        env = shell.select_c_utf8_locale()
        command = tuple(command)  # For consistency when testing.
        process = Popen(command, stdout=PIPE, stderr=PIPE, env=env)
        stdout, _ = process.communicate()
        stdout = stdout.decode("utf-8").strip()
        for error, error_info in IPMI_ERRORS.items():
            # ipmipower dumps errors to stdout
            if error in stdout:
                raise error_info.get('exception')(error_info.get('message'))
        if process.returncode != 0:
            raise PowerError(
                "Failed to power %s %s: %s" % (
                    power_change, power_address, stdout))
        match = re.search(":\s*(on|off)", stdout)
        return stdout if match is None else match.group(1)

    def _issue_ipmi_command(
            self, power_change, power_address=None, power_user=None,
            power_pass=None, power_driver=None, power_off_mode=None,
            ipmipower=None, ipmi_chassis_config=None, mac_address=None,
            **extra):
        """Issue command to ipmipower, for the given system."""
        # This script deliberately does not check the current power state
        # before issuing the requested power command. See bug 1171418 for an
        # explanation.

        if (is_power_parameter_set(mac_address) and not
                is_power_parameter_set(power_address)):
            power_address = find_ip_via_arp(mac_address)

        # The `-W opensesspriv` workaround is required on many BMCs, and
        # should have no impact on BMCs that don't require it.
        # See https://bugs.launchpad.net/maas/+bug/1287964
        ipmi_chassis_config_command = [
            ipmi_chassis_config, '-W', 'opensesspriv']
        ipmipower_command = [
            ipmipower, '-W', 'opensesspriv']

        # Arguments in common between chassis config and power control. See
        # https://launchpad.net/bugs/1053391 for details of modifying the
        # command for power_driver and power_user.
        common_args = []
        if is_power_parameter_set(power_driver):
            common_args.extend(("--driver-type", power_driver))
        common_args.extend(('-h', power_address))
        if is_power_parameter_set(power_user):
            common_args.extend(("-u", power_user))
        common_args.extend(('-p', power_pass))

        # Update the chassis config and power commands.
        ipmi_chassis_config_command.extend(common_args)
        ipmi_chassis_config_command.append('--commit')
        ipmipower_command.extend(common_args)

        # Before changing state run the chassis config command.
        if power_change in ("on", "off"):
            self._issue_ipmi_chassis_config_command(
                ipmi_chassis_config_command, power_change, power_address)

        # Additional arguments for the power command.
        if power_change == 'on':
            ipmipower_command.append('--cycle')
            ipmipower_command.append('--on-if-off')
        elif power_change == 'off':
            if power_off_mode == 'soft':
                ipmipower_command.append('--soft')
            else:
                ipmipower_command.append('--off')
        elif power_change == 'query':
            ipmipower_command.append('--stat')

        # Update or query the power state.
        return self._issue_ipmipower_command(
            ipmipower_command, power_change, power_address)

    def power_on(self, system_id, context):
        self._issue_ipmi_command('on', **context)

    def power_off(self, system_id, context):
        self._issue_ipmi_command('off', **context)

    def power_query(self, system_id, context):
        return self._issue_ipmi_command('query', **context)
