# Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""American Power Conversion (APC) Power Driver.

Support for managing American Power Conversion (APC) PDU outlets via SNMP.
"""

__all__ = []

import re
from subprocess import (
    PIPE,
    Popen,
)
from time import sleep

from provisioningserver.drivers.power import (
    PowerActionError,
    PowerDriver,
)
from provisioningserver.utils import shell
from provisioningserver.utils.shell import select_c_utf8_locale


COMMON_ARGS = '-c private -v1 %s .1.3.6.1.4.1.318.1.1.12.3.3.1.1.4.%s'


class APCState:
    ON = '1'
    OFF = '2'


class APCPowerDriver(PowerDriver):

    name = 'apc'
    description = "APC Power Driver."
    settings = []

    def detect_missing_packages(self):
        binary, package = ['snmpset', 'snmp']
        if not shell.has_command_available(binary):
            return [package]
        return []

    def run_process(self, command):
        """Run SNMP command in subprocess."""
        proc = Popen(
            command.split(), stdout=PIPE, stderr=PIPE,
            env=select_c_utf8_locale())
        stdout, stderr = proc.communicate()
        stdout = stdout.decode("utf-8")
        stderr = stderr.decode("utf-8")
        if proc.returncode != 0:
            raise PowerActionError(
                "APC Power Driver external process error for command %s: %s"
                % (command, stderr))
        match = re.search("INTEGER:\s*([1-2])", stdout)
        if match is None:
            raise PowerActionError(
                "APC Power Driver unable to extract outlet power state"
                " from: %s" % stdout)
        else:
            return match.group(1)

    def power_on(self, system_id, context):
        """Power on Apc outlet."""
        if self.power_query(system_id, context) == 'on':
            self.power_off(system_id, context)
        sleep(float(context['power_on_delay']))
        self.run_process('snmpset ' + COMMON_ARGS % (
            context['power_address'], context['node_outlet']) + ' i 1')

    def power_off(self, system_id, context):
        """Power off APC outlet."""
        self.run_process('snmpset ' + COMMON_ARGS % (
            context['power_address'], context['node_outlet']) + ' i 2')

    def power_query(self, system_id, context):
        """Power query APC outlet."""
        power_state = self.run_process(
            'snmpget ' + COMMON_ARGS % (
                context['power_address'], context['node_outlet']))
        if power_state == APCState.OFF:
            return 'off'
        elif power_state == APCState.ON:
            return 'on'
        else:
            raise PowerActionError(
                "APC Power Driver retrieved unknown power state: %r"
                % power_state)
