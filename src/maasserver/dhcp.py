# Copyright 2012-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""DHCP management module."""

__all__ = [
    'configure_dhcp',
    'validate_dhcp_config',
    ]

from collections import defaultdict
from operator import itemgetter
from typing import Iterable

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from maasserver.enum import (
    INTERFACE_TYPE,
    IPADDRESS_TYPE,
    IPRANGE_TYPE,
    SERVICE_STATUS,
)
from maasserver.exceptions import (
    DHCPConfigurationError,
    UnresolvableHost,
)
from maasserver.models import (
    Config,
    DHCPSnippet,
    Domain,
    RackController,
    Service,
    StaticIPAddress,
)
from maasserver.rpc import (
    getAllClients,
    getClientFor,
    getRandomClient,
)
from maasserver.utils.orm import transactional
from maasserver.utils.threads import deferToDatabase
from netaddr import IPAddress
from provisioningserver.dhcp.omshell import generate_omapi_key
from provisioningserver.rpc.cluster import (
    ConfigureDHCPv4,
    ConfigureDHCPv4_V2,
    ConfigureDHCPv6,
    ConfigureDHCPv6_V2,
    ValidateDHCPv4Config,
    ValidateDHCPv4Config_V2,
    ValidateDHCPv6Config,
    ValidateDHCPv6Config_V2,
)
from provisioningserver.rpc.dhcp import downgrade_shared_networks
from provisioningserver.rpc.exceptions import NoConnectionsAvailable
from provisioningserver.utils import typed
from provisioningserver.utils.text import split_string_list
from provisioningserver.utils.twisted import (
    asynchronous,
    synchronous,
)
from twisted.internet.defer import inlineCallbacks
from twisted.protocols import amp
from twisted.python import log


def get_omapi_key():
    """Return the OMAPI key for all DHCP servers that are ran by MAAS."""
    key = Config.objects.get_config("omapi_key")
    if key is None or key == '':
        key = generate_omapi_key()
        Config.objects.set_config("omapi_key", key)
    return key


def split_ipv4_ipv6_subnets(subnets):
    """Divide `subnets` into IPv4 ones and IPv6 ones.

    :param subnets: A sequence of subnets.
    :return: A tuple of two separate sequences: IPv4 subnets and IPv6 subnets.
    """
    split = defaultdict(list)
    for subnet in subnets:
        split[subnet.get_ipnetwork().version].append(subnet)
    assert len(split) <= 2, (
        "Unexpected IP version(s): %s" % ', '.join(list(split.keys())))
    return split[4], split[6]


def ip_is_sticky_or_auto(ip_address):
    """Return True if the `ip_address` alloc_type is STICKY or AUTO."""
    return ip_address.alloc_type in [
        IPADDRESS_TYPE.STICKY, IPADDRESS_TYPE.AUTO]


def get_best_interface(interfaces):
    """Return `Interface` from `interfaces` that is the best.

    This is used by `get_subnet_to_interface_mapping` to select the very best
    interface on a `Subnet`. Bond interfaces are selected over physical/vlan
    interfaces.
    """
    best_interface = None
    for interface in interfaces:
        if best_interface is None:
            best_interface = interface
        elif (best_interface.type == INTERFACE_TYPE.PHYSICAL and
                interface.type == INTERFACE_TYPE.BOND):
            best_interface = interface
        elif (best_interface.type == INTERFACE_TYPE.VLAN and
                interface.type == INTERFACE_TYPE.PHYSICAL):
            best_interface = interface
    return best_interface


def ip_is_version(ip_address, ip_version):
    """Return True if `ip_address` is the same IP version as `ip_version`."""
    return (
        ip_address.ip is not None and
        ip_address.ip != "" and
        IPAddress(ip_address.ip).version == ip_version)


def _key_interface_subnet_dynamic_range_count(interface):
    """Return the number of dynamic ranges for the subnet on the interface."""
    count = 0
    for ip_address in interface.ip_addresses.all():
        for ip_range in ip_address.subnet.iprange_set.all():
            if ip_range.type == IPRANGE_TYPE.DYNAMIC:
                count += 1
    return count


def get_interfaces_with_ip_on_vlan(rack_controller, vlan, ip_version):
    """Return a list of interfaces that have an assigned IP address on `vlan`.

    The assigned IP address needs to be of same `ip_version`. Only a list of
    STICKY or AUTO addresses will be returned, unless none exists which will
    fallback to DISCOVERED addresses.

    The interfaces will be ordered so that interfaces with IP address on
    subnets for the VLAN that have dynamic IP ranges defined.
    """
    interfaces_with_static = []
    interfaces_with_discovered = []
    for interface in rack_controller.interface_set.all().prefetch_related(
            "ip_addresses__subnet__vlan",
            "ip_addresses__subnet__iprange_set"):
        for ip_address in interface.ip_addresses.all():
            if ip_address.alloc_type in [
                    IPADDRESS_TYPE.AUTO, IPADDRESS_TYPE.STICKY]:
                if (ip_is_version(ip_address, ip_version) and
                        ip_address.subnet is not None and
                        ip_address.subnet.vlan == vlan):
                    interfaces_with_static.append(interface)
                    break
            elif ip_address.alloc_type == IPADDRESS_TYPE.DISCOVERED:
                if (ip_is_version(ip_address, ip_version) and
                        ip_address.subnet is not None and
                        ip_address.subnet.vlan == vlan):
                    interfaces_with_discovered.append(interface)
                    break
    if len(interfaces_with_static) == 1:
        return interfaces_with_static
    elif len(interfaces_with_static) > 1:
        return sorted(
            interfaces_with_static,
            key=_key_interface_subnet_dynamic_range_count,
            reverse=True)
    elif len(interfaces_with_discovered) == 1:
        return interfaces_with_discovered
    elif len(interfaces_with_discovered) > 1:
        return sorted(
            interfaces_with_discovered,
            key=_key_interface_subnet_dynamic_range_count,
            reverse=True)
    else:
        return []


def get_managed_vlans_for(rack_controller):
    """Return list of `VLAN` for the `rack_controller` when DHCP is enabled and
    `rack_controller` is either the `primary_rack` or the `secondary_rack`.
    """
    interfaces = rack_controller.interface_set.filter(
        Q(vlan__dhcp_on=True) & (
            Q(vlan__primary_rack=rack_controller) |
            Q(vlan__secondary_rack=rack_controller))).select_related("vlan")
    return {
        interface.vlan
        for interface in interfaces
    }


def ip_is_on_vlan(ip_address, vlan):
    """Return True if `ip_address` is on `vlan`."""
    return (
        ip_is_sticky_or_auto(ip_address) and
        ip_address.subnet.vlan_id == vlan.id and
        ip_address.ip is not None and
        ip_address.ip != "")


def get_ip_address_for_interface(interface, vlan):
    """Return the IP address for `interface` on `vlan`."""
    for ip_address in interface.ip_addresses.all():
        if ip_is_on_vlan(ip_address, vlan):
            return ip_address
    return None


def get_ip_address_for_rack_controller(rack_controller, vlan):
    """Return the IP address for `rack_controller` on `vlan`."""
    # First we build a list of all interfaces that have an IP address
    # on that vlan. Then we pick the best interface for that vlan
    # based on the `get_best_interface` function.
    interfaces = rack_controller.interface_set.all().prefetch_related(
        "ip_addresses__subnet")
    matching_interfaces = set()
    for interface in interfaces:
        for ip_address in interface.ip_addresses.all():
            if ip_is_on_vlan(ip_address, vlan):
                matching_interfaces.add(interface)
    interface = get_best_interface(matching_interfaces)
    return get_ip_address_for_interface(interface, vlan)


def make_interface_hostname(interface):
    """Return the host decleration name for DHCPD for this `interface`."""
    interface_name = interface.name.replace(".", "-")
    if interface.type == INTERFACE_TYPE.UNKNOWN and interface.node is None:
        return "unknown-%d-%s" % (interface.id, interface_name)
    else:
        return "%s-%s" % (interface.node.hostname, interface_name)


def make_dhcp_snippet(dhcp_snippet):
    """Return the DHCPSnippet as a dictionary."""
    return {
        "name": dhcp_snippet.name,
        "description": dhcp_snippet.description,
        "value": dhcp_snippet.value.data,
    }


def make_hosts_for_subnets(subnets, nodes_dhcp_snippets: list=None):
    """Return list of host entries to create in the DHCP configuration for the
    given `subnets`.
    """
    if nodes_dhcp_snippets is None:
        nodes_dhcp_snippets = []

    def get_dhcp_snippets_for_interface(interface):
        dhcp_snippets = list()
        for dhcp_snippet in nodes_dhcp_snippets:
            if dhcp_snippet.node == interface.node:
                dhcp_snippets.append(make_dhcp_snippet(dhcp_snippet))
        return dhcp_snippets

    sips = StaticIPAddress.objects.filter(
        alloc_type__in=[
            IPADDRESS_TYPE.AUTO,
            IPADDRESS_TYPE.STICKY,
            IPADDRESS_TYPE.USER_RESERVED,
            ],
        subnet__in=subnets, ip__isnull=False).order_by('id')
    hosts = []
    interface_ids = set()
    for sip in sips:
        # Skip blank IP addresses.
        if sip.ip == '':
            continue

        # Add all interfaces attached to this IP address.
        for interface in sip.interface_set.order_by('id'):
            # Only allow an interface to be in hosts once.
            if interface.id in interface_ids:
                continue
            else:
                interface_ids.add(interface.id)

            # Bond interfaces get all its parent interfaces created as
            # hosts as well.
            if interface.type == INTERFACE_TYPE.BOND:
                for parent in interface.parents.all():
                    # Only add parents that MAC address is different from
                    # from the bond.
                    if parent.mac_address != interface.mac_address:
                        interface_ids.add(parent.id)
                        hosts.append({
                            'host': make_interface_hostname(parent),
                            'mac': str(parent.mac_address),
                            'ip': str(sip.ip),
                            'dhcp_snippets': get_dhcp_snippets_for_interface(
                                parent),
                        })
                hosts.append({
                    'host': make_interface_hostname(interface),
                    'mac': str(interface.mac_address),
                    'ip': str(sip.ip),
                    'dhcp_snippets': get_dhcp_snippets_for_interface(
                        interface),
                })
            else:
                hosts.append({
                    'host': make_interface_hostname(interface),
                    'mac': str(interface.mac_address),
                    'ip': str(sip.ip),
                    'dhcp_snippets': get_dhcp_snippets_for_interface(
                        interface),
                })
    return hosts


def make_pools_for_subnet(subnet, failover_peer=None):
    """Return list of pools to create in the DHCP config for `subnet`."""
    pools = []
    for ip_range in subnet.get_dynamic_ranges().order_by('id'):
        pool = {
            "ip_range_low": ip_range.start_ip,
            "ip_range_high": ip_range.end_ip,
        }
        if failover_peer is not None:
            pool["failover_peer"] = failover_peer
        pools.append(pool)
    return pools


@typed
def make_subnet_config(
        rack_controller, subnet, maas_dns_server, ntp_servers: list,
        default_domain, failover_peer=None, subnets_dhcp_snippets: list=None):
    """Return DHCP subnet configuration dict for a rack interface."""
    ip_network = subnet.get_ipnetwork()
    if subnet.dns_servers is not None and len(subnet.dns_servers) > 0:
        # Replace MAAS DNS with the servers defined on the subnet.
        dns_servers = [IPAddress(server) for server in subnet.dns_servers]
    elif maas_dns_server is not None and len(maas_dns_server) > 0:
        dns_servers = [IPAddress(maas_dns_server)]
    else:
        dns_servers = []
    if subnets_dhcp_snippets is None:
        subnets_dhcp_snippets = []
    return {
        'subnet': str(ip_network.network),
        'subnet_mask': str(ip_network.netmask),
        'subnet_cidr': str(ip_network.cidr),
        'broadcast_ip': str(ip_network.broadcast),
        'router_ip': (
            '' if not subnet.gateway_ip
            else str(subnet.gateway_ip)),
        'dns_servers': dns_servers,
        'ntp_servers': ntp_servers,
        'domain_name': default_domain.name,
        'pools': make_pools_for_subnet(subnet, failover_peer),
        'dhcp_snippets': [
            make_dhcp_snippet(dhcp_snippet)
            for dhcp_snippet in subnets_dhcp_snippets
            if dhcp_snippet.subnet == subnet
            ],
        }


def make_failover_peer_config(vlan, rack_controller):
    """Return DHCP failover peer configuration dict for a rack controller."""
    is_primary = vlan.primary_rack_id == rack_controller.id
    interface_ip_address = get_ip_address_for_rack_controller(
        rack_controller, vlan)
    if is_primary:
        peer_address = get_ip_address_for_rack_controller(
            vlan.secondary_rack, vlan)
    else:
        peer_address = get_ip_address_for_rack_controller(
            vlan.primary_rack, vlan)
    name = "failover-vlan-%d" % vlan.id
    return name, {
        "name": name,
        "mode": "primary" if is_primary else "secondary",
        "address": str(interface_ip_address.ip),
        "peer_address": str(peer_address.ip),
    }


@typed
def get_dhcp_configure_for(
        ip_version: int, rack_controller, vlan, subnets: list,
        ntp_servers: list, domain, dhcp_snippets: Iterable=None):
    """Get the DHCP configuration for `ip_version`."""
    # Circular imports.
    from maasserver.dns.zonegenerator import get_dns_server_address

    try:
        maas_dns_server = get_dns_server_address(
            rack_controller, ipv4=(ip_version == 4), ipv6=(ip_version == 6))
    except UnresolvableHost:
        maas_dns_server = None

    # Select the best interface for this VLAN. This is an interface that
    # at least has an IP address.
    interfaces = get_interfaces_with_ip_on_vlan(
        rack_controller, vlan, ip_version)
    interface = get_best_interface(interfaces)
    if interface is None:
        raise DHCPConfigurationError(
            "No IPv%d interface on rack controller '%s' has an IP address on "
            "any subnet on VLAN '%s.%d'." % (
                ip_version, rack_controller.hostname, vlan.fabric.name,
                vlan.vid))

    # Generate the failover peer for this VLAN.
    if vlan.secondary_rack_id is not None:
        peer_name, peer_config = make_failover_peer_config(
            vlan, rack_controller)
    else:
        peer_name, peer_config = None, None

    if dhcp_snippets is None:
        dhcp_snippets = []

    subnets_dhcp_snippets = [
        dhcp_snippet for dhcp_snippet in dhcp_snippets
        if dhcp_snippet.subnet is not None]
    nodes_dhcp_snippets = [
        dhcp_snippet for dhcp_snippet in dhcp_snippets
        if dhcp_snippet.node is not None]

    # Generate the shared network configurations.
    subnet_configs = []
    hosts = []
    for subnet in subnets:
        subnet_configs.append(
            make_subnet_config(
                rack_controller, subnet, maas_dns_server, ntp_servers,
                domain, peer_name, subnets_dhcp_snippets))

    # Generate the hosts for all subnets.
    hosts = make_hosts_for_subnets(subnets, nodes_dhcp_snippets)
    return (
        peer_config, sorted(subnet_configs, key=itemgetter("subnet")),
        hosts, interface.name)


@synchronous
@transactional
def get_dhcp_configuration(rack_controller, test_dhcp_snippet=None):
    """Return tuple with IPv4 and IPv6 configurations for the
    rack controller."""
    # Get list of all vlans that are being managed by the rack controller.
    vlans = get_managed_vlans_for(rack_controller)

    # Group the subnets on each VLAN into IPv4 and IPv6 subnets.
    vlan_subnets = {
        vlan: split_ipv4_ipv6_subnets(vlan.subnet_set.all())
        for vlan in vlans
    }

    # Get the list of all DHCP snippets so we only have to query the database
    # 1 + (the number of DHCP snippets used in this VLAN) instead of
    # 1 + (the number of subnets in this VLAN) +
    #     (the number of nodes in this VLAN)
    dhcp_snippets = DHCPSnippet.objects.filter(enabled=True)
    # If we're testing a DHCP Snippet insert it into our list
    if test_dhcp_snippet is not None:
        dhcp_snippets = list(dhcp_snippets)
        replaced_snippet = False
        # If its an existing DHCPSnippet with its contents being modified
        # replace it with the new values and test
        for i, dhcp_snippet in enumerate(dhcp_snippets):
            if dhcp_snippet.id == test_dhcp_snippet.id:
                dhcp_snippets[i] = test_dhcp_snippet
                replaced_snippet = True
                break
        # If the snippet wasn't updated its either new or testing a currently
        # disabled snippet
        if not replaced_snippet:
            dhcp_snippets.append(test_dhcp_snippet)
    global_dhcp_snippets = [
        make_dhcp_snippet(dhcp_snippet)
        for dhcp_snippet in dhcp_snippets
        if dhcp_snippet.node is None and dhcp_snippet.subnet is None
        ]

    # Configure both DHCPv4 and DHCPv6 on the rack controller.
    failover_peers_v4 = []
    shared_networks_v4 = []
    hosts_v4 = []
    interfaces_v4 = set()
    failover_peers_v6 = []
    shared_networks_v6 = []
    hosts_v6 = []
    interfaces_v6 = set()
    ntp_servers = Config.objects.get_config("ntp_servers")
    ntp_servers = list(split_string_list(ntp_servers))
    default_domain = Domain.objects.get_default_domain()
    for vlan, (subnets_v4, subnets_v6) in vlan_subnets.items():
        # IPv4
        if len(subnets_v4) > 0:
            try:
                config = get_dhcp_configure_for(
                    4, rack_controller, vlan, subnets_v4, ntp_servers,
                    default_domain, dhcp_snippets)
            except DHCPConfigurationError as e:
                # XXX bug #1602412: this silently breaks DHCPv4, but we cannot
                # allow it to crash here since DHCPv6 might be able to run.
                # This error may be irrelevant if there is an IPv4 network in
                # the MAAS model which is not configured on the rack, and the
                # user only wants to serve DHCPv6. But it is still something
                # worth noting, so log it and continue.
                log.err(e)
            else:
                failover_peer, subnets, hosts, interface = config
                if failover_peer is not None:
                    failover_peers_v4.append(failover_peer)
                shared_networks_v4.append({
                    "name": "vlan-%d" % vlan.id,
                    "subnets": subnets,
                })
                hosts_v4.extend(hosts)
                interfaces_v4.add(interface)
        # IPv6
        if len(subnets_v6) > 0:
            try:
                config = get_dhcp_configure_for(
                    6, rack_controller, vlan, subnets_v6,
                    ntp_servers, default_domain, dhcp_snippets)
            except DHCPConfigurationError as e:
                # XXX bug #1602412: this silently breaks DHCPv6, but we cannot
                # allow it to crash here since DHCPv4 might be able to run.
                # This error may be irrelevant if there is an IPv6 network in
                # the MAAS model which is not configured on the rack, and the
                # user only wants to serve DHCPv4. But it is still something
                # worth noting, so log it and continue.
                log.err(e)
            else:
                failover_peer, subnets, hosts, interface = config
                if failover_peer is not None:
                    failover_peers_v6.append(failover_peer)
                shared_networks_v6.append({
                    "name": "vlan-%d" % vlan.id,
                    "subnets": subnets,
                })
                hosts_v6.extend(hosts)
                interfaces_v6.add(interface)
    return (
        get_omapi_key(),
        failover_peers_v4, shared_networks_v4, hosts_v4, interfaces_v4,
        failover_peers_v6, shared_networks_v6, hosts_v6, interfaces_v6,
        global_dhcp_snippets)


@asynchronous
@inlineCallbacks
def configure_dhcp(rack_controller):
    """Write the DHCP configuration files and restart the DHCP servers.

    :raises: :py:class:`~.exceptions.NoConnectionsAvailable` when there
        are no open connections to the specified cluster controller.
    """
    # Let's get this out of the way first up shall we?
    if not settings.DHCP_CONNECT:
        # For the uninitiated, DHCP_CONNECT is set, by default, to False
        # in all tests and True in non-tests.  This avoids unnecessary
        # calls to async tasks.
        return

    # Get the client early; it's a cheap operation that may raise an
    # exception, meaning we can avoid some work if it fails.
    client = yield getClientFor(rack_controller.system_id)

    # Get configuration for both IPv4 and IPv6.
    (omapi_key, failover_peers_v4, shared_networks_v4, hosts_v4, interfaces_v4,
     failover_peers_v6, shared_networks_v6, hosts_v6, interfaces_v6,
     global_dhcp_snippets) = (
        yield deferToDatabase(get_dhcp_configuration, rack_controller))

    # Fix interfaces to go over the wire.
    interfaces_v4 = [
        {"name": name}
        for name in interfaces_v4
    ]
    interfaces_v6 = [
        {"name": name}
        for name in interfaces_v6
    ]

    # Configure both IPv4 and IPv6.
    ipv4_exc, ipv6_exc = None, None
    ipv4_status, ipv6_status = SERVICE_STATUS.UNKNOWN, SERVICE_STATUS.UNKNOWN

    try:
        yield _perform_dhcp_config(
            client, ConfigureDHCPv4_V2, ConfigureDHCPv4, omapi_key=omapi_key,
            failover_peers=failover_peers_v4, interfaces=interfaces_v4,
            shared_networks=shared_networks_v4, hosts=hosts_v4,
            global_dhcp_snippets=global_dhcp_snippets)
    except Exception as exc:
        ipv4_exc = exc
        ipv4_status = SERVICE_STATUS.DEAD
        log.err(
            "Error configuring DHCPv4 on rack controller '%s': %s" % (
                rack_controller.system_id, exc))
    else:
        if len(shared_networks_v4) > 0:
            ipv4_status = SERVICE_STATUS.RUNNING
        else:
            ipv4_status = SERVICE_STATUS.OFF
        log.msg(
            "Successfully configured DHCPv4 on rack controller '%s'." % (
                rack_controller.system_id))

    try:
        yield _perform_dhcp_config(
            client, ConfigureDHCPv6_V2, ConfigureDHCPv6, omapi_key=omapi_key,
            failover_peers=failover_peers_v6, interfaces=interfaces_v6,
            hosts=hosts_v6, global_dhcp_snippets=global_dhcp_snippets,
            shared_networks=shared_networks_v6)
    except Exception as exc:
        ipv6_exc = exc
        ipv6_status = SERVICE_STATUS.DEAD
        log.err(
            "Error configuring DHCPv6 on rack controller '%s': %s" % (
                rack_controller.system_id, exc))
    else:
        if len(shared_networks_v6) > 0:
            ipv6_status = SERVICE_STATUS.RUNNING
        else:
            ipv6_status = SERVICE_STATUS.OFF
        log.msg(
            "Successfully configured DHCPv6 on rack controller '%s'." % (
                rack_controller.system_id))

    # Update the status for both services so the user is always seeing the
    # most up to date status.
    @transactional
    def update_services():
        if ipv4_exc is None:
            ipv4_status_info = ""
        else:
            ipv4_status_info = str(ipv4_exc)
        if ipv6_exc is None:
            ipv6_status_info = ""
        else:
            ipv6_status_info = str(ipv6_exc)
        Service.objects.update_service_for(
            rack_controller, "dhcpd", ipv4_status, ipv4_status_info)
        Service.objects.update_service_for(
            rack_controller, "dhcpd6", ipv6_status, ipv6_status_info)
    yield deferToDatabase(update_services)


def validate_dhcp_config(test_dhcp_snippet=None):
    """Validate a DHCPD config with uncommitted values.

    Gathers the DHCPD config from what is committed in the database, as well as
    DHCPD config which needs to be validated, and asks a rack controller to
    validate. Testing is done with dhcpd's builtin validation flag.

    :param test_dhcp_snippet: A DHCPSnippet which has not yet been committed to
        the database and needs to be validated.
    """
    # XXX ltrager 2016-03-28 - This only tests the existing config with new
    # DHCPSnippets but could be expanded to test changes to the config(e.g
    # subnets, omapi_key, interfaces, etc) before they are commited.

    def find_connected_rack(racks):
        connected_racks = [client.ident for client in getAllClients()]
        for rack in racks:
            if rack.system_id in connected_racks:
                return rack
        # The dhcpd.conf config rendered on a rack controller only contains
        # subnets and interfaces which can connect to that rack controller.
        # If no rack controller was found picking a random rack controller
        # which is connected will result in testing a config which does
        # not contain the values we are trying to test.
        raise ValidationError(
            'Unable to validate DHCP config, '
            'no available rack controller connected.')

    rack_controller = None
    # Test on the rack controller where the DHCPSnippet will be used
    if test_dhcp_snippet is not None:
        if test_dhcp_snippet.subnet is not None:
            rack_controller = find_connected_rack(
                RackController.objects.filter_by_subnets(
                    [test_dhcp_snippet.subnet])
            )
        elif test_dhcp_snippet.node is not None:
            rack_controller = find_connected_rack(
                test_dhcp_snippet.node.get_boot_rack_controllers()
            )
    # If no rack controller is linked to the DHCPSnippet its a global DHCP
    # snippet which we can test anywhere.
    if rack_controller is None:
        try:
            client = getRandomClient()
        except NoConnectionsAvailable:
            raise ValidationError(
                'Unable to validate DHCP config, '
                'no available rack controller connected.')
        rack_controller = RackController.objects.get(system_id=client.ident)
    else:
        try:
            client = getClientFor(rack_controller.system_id)
        except NoConnectionsAvailable:
            raise ValidationError(
                'Unable to validate DHCP config, '
                'no available rack controller connected.')
        rack_controller = RackController.objects.get(system_id=client.ident)

    # Get configuration for both IPv4 and IPv6.
    (omapi_key, failover_peers_v4, shared_networks_v4, hosts_v4, interfaces_v4,
     failover_peers_v6, shared_networks_v6, hosts_v6, interfaces_v6,
     global_dhcp_snippets) = get_dhcp_configuration(
        rack_controller, test_dhcp_snippet)

    # Fix interfaces to go over the wire.
    interfaces_v4 = [
        {"name": name}
        for name in interfaces_v4
    ]
    interfaces_v6 = [
        {"name": name}
        for name in interfaces_v6
    ]

    # Validate both IPv4 and IPv6.
    v4_args = dict(
        omapi_key=omapi_key, failover_peers=failover_peers_v4, hosts=hosts_v4,
        interfaces=interfaces_v4, global_dhcp_snippets=global_dhcp_snippets,
        shared_networks=shared_networks_v4)
    v6_args = dict(
        omapi_key=omapi_key, failover_peers=failover_peers_v6, hosts=hosts_v6,
        interfaces=interfaces_v6, global_dhcp_snippets=global_dhcp_snippets,
        shared_networks=shared_networks_v6)

    # XXX: These remote calls can hold transactions open for a prolonged
    # period. This is bad for concurrency and scaling.
    v4_response = _validate_dhcp_config_v4(client, **v4_args).wait(30)
    v6_response = _validate_dhcp_config_v6(client, **v6_args).wait(30)

    # Deduplicate errors between IPv4 and IPv6
    known_errors = []
    unique_errors = []
    for errors in (v4_response['errors'], v6_response['errors']):
        if errors is None:
            continue
        for error in errors:
            hash = "%s - %s" % (error['line'], error['error'])
            if hash not in known_errors:
                known_errors.append(hash)
                unique_errors.append(error)
    return unique_errors


def _validate_dhcp_config_v4(client, **args):
    """See `_validate_dhcp_config_vx`."""
    return _perform_dhcp_config(
        client, ValidateDHCPv4Config_V2, ValidateDHCPv4Config, **args)


def _validate_dhcp_config_v6(client, **args):
    """See `_validate_dhcp_config_vx`."""
    return _perform_dhcp_config(
        client, ValidateDHCPv6Config_V2, ValidateDHCPv6Config, **args)


@asynchronous
def _perform_dhcp_config(
        client, v2_command, v1_command, *, shared_networks, **args):
    """Call `v2_command` then `v1_command`...

    ... if the former is not recognised. This allows interoperability between
    a region that's newer than the rack controller.

    :param client: An RPC client.
    :param v2_command: The RPC command to attempt first.
    :param v1_command: The RPC command to attempt second.
    :param shared_networks: The shared networks argument for `v2_command` and
        `v1_command`. If `v2_command` is not handled by the remote side, this
        structure will be downgraded in place.
    :param args: Remaining arguments for `v2_command` and `v1_command`.
    """
    def call(command):
        return client(command, shared_networks=shared_networks, **args)

    def maybeDowngrade(failure):
        if failure.check(amp.UnhandledCommand):
            downgrade_shared_networks(shared_networks)
            return call(v1_command)
        else:
            return failure

    return call(v2_command).addErrback(maybeDowngrade)
