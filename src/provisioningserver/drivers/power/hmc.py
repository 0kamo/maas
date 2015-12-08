# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Moonshot HP iLO Chassis Power Driver."""

__all__ = []

from provisioningserver.drivers.hardware.hmc import (
    power_control_hmc,
    power_state_hmc,
)
from provisioningserver.drivers.power import PowerDriver


def extract_hmc_parameters(context):
    ip = context.get('power_address')
    username = context.get('power_user')
    password = context.get('power_pass')
    server_name = context.get('server_name')
    lpar = context.get('lpar')
    return ip, username, password, server_name, lpar


class HMCPowerDriver(PowerDriver):

    name = 'hmc'
    description = "IBM Hardware Management Console Power Driver."
    settings = []

    def detect_missing_packages(self):
        # uses pure-python paramiko ssh client - nothing to look for!
        return []

    def power_on(self, system_id, context):
        ip, username, password, server_name, lpar = (
            extract_hmc_parameters(context))
        power_control_hmc(
            ip, username, password, server_name, lpar, power_change='on')

    def power_off(self, system_id, context):
        ip, username, password, server_name, lpar = (
            extract_hmc_parameters(context))
        power_control_hmc(
            ip, username, password, server_name, lpar, power_change='off')

    def power_query(self, system_id, context):
        ip, username, password, server_name, lpar = (
            extract_hmc_parameters(context))
        return power_state_hmc(ip, username, password, server_name, lpar)
