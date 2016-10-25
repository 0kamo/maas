# Copyright 2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Networks monitoring service for rack controllers."""

__all__ = [
    "RackNetworksMonitoringService",
]

from provisioningserver.logger import get_maas_logger
from provisioningserver.rpc.region import (
    GetDiscoveryState,
    ReportMDNSEntries,
    ReportNeighbours,
    RequestRackRefresh,
    UpdateInterfaces,
)
from provisioningserver.utils.services import NetworksMonitoringService


maaslog = get_maas_logger("networks.monitor")


class RackNetworksMonitoringService(NetworksMonitoringService):
    """Rack service to monitor network interfaces for configuration changes."""

    def __init__(self, clientService, *args, **kwargs):
        super(RackNetworksMonitoringService, self).__init__(*args, **kwargs)
        self.clientService = clientService

    def getDiscoveryState(self):
        """Get the discovery state from the region."""
        client = self.clientService.getClient()
        if self._recorded is None:
            # Wait until the rack has refreshed.
            return {}
        else:
            d = client(
                GetDiscoveryState, system_id=client.localIdent)
            d.addCallback(lambda args: args['interfaces'])
            return d

    def recordInterfaces(self, interfaces):
        """Record the interfaces information to the region."""
        client = self.clientService.getClient()
        # On first run perform a refresh
        if self._recorded is None:
            return client(RequestRackRefresh, system_id=client.localIdent)
        else:
            return client(
                UpdateInterfaces, system_id=client.localIdent,
                interfaces=interfaces)

    def reportNeighbours(self, neighbours):
        """Report neighbour information to the region."""
        client = self.clientService.getClient()
        return client(
            ReportNeighbours, system_id=client.localIdent,
            neighbours=neighbours)

    def reportMDNSEntries(self, mdns):
        """Report mDNS entries to the region."""
        client = self.clientService.getClient()
        return client(
            ReportMDNSEntries, system_id=client.localIdent, mdns=mdns)
