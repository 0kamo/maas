# Copyright 2012-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for DHCP management."""

__all__ = []

"""
For some reason unittest.skip is not working on the class level. To speed up
removal of NodeGroup. This whole test case is commented out. Once wiring up
the listener to enable new DHCP HA is performed this should be removed as some
of this module will be used later.

from operator import itemgetter
import random
import unittest

from django.conf import settings
from django.db import transaction
from fixtures import LoggerFixture
from maasserver import dhcp
from maasserver.dhcp import (
    configure_dhcp,
    consolidator,
    do_configure_dhcp,
    make_subnet_config,
    split_ipv4_ipv6_interfaces,
)
from maasserver.enum import (
    INTERFACE_TYPE,
    IPADDRESS_TYPE,
)
from maasserver.models import Config
from maasserver.rpc import getClientFor
from maasserver.rpc.testing.fixtures import MockLiveRegionToClusterRPCFixture
from maasserver.testing.eventloop import (
    RegionEventLoopFixture,
    RunningEventLoopFixture,
)
from maasserver.testing.factory import factory
from maasserver.testing.testcase import (
    MAASServerTestCase,
    MAASTransactionServerTestCase,
)
from maasserver.utils.orm import post_commit_hooks
from maastesting.matchers import (
    MockCalledOnceWith,
    MockCallsMatch,
    MockNotCalled,
)
from maastesting.twisted import always_succeed_with
from mock import (
    ANY,
    call,
    sentinel,
)
from netaddr import (
    IPAddress,
    IPNetwork,
)
from provisioningserver.rpc.cluster import (
    ConfigureDHCPv4,
    ConfigureDHCPv6,
)
from provisioningserver.utils.url import compose_URL
from testtools.matchers import (
    AllMatch,
    ContainsAll,
    ContainsDict,
    Equals,
    IsInstance,
    MatchesStructure,
    Not,
)


class TestSplitIPv4IPv6Interfaces(MAASServerTestCase):
    "Tests for `split_ipv4_ipv6_interfaces`.""

    def make_ipv4_interface(self, nodegroup):
        subnet = factory.make_Subnet(
            cidr=str(factory.make_ipv4_network().cidr))
        return factory.make_NodeGroupInterface(
            nodegroup, subnet=subnet)

    def make_ipv6_interface(self, nodegroup):
        subnet = factory.make_Subnet(
            cidr=str(factory.make_ipv6_network().cidr))
        return factory.make_NodeGroupInterface(
            nodegroup, subnet=subnet)

    def test__separates_IPv4_from_IPv6_interfaces(self):
        nodegroup = factory.make_NodeGroup()
        # Create 0-2 IPv4 cluster interfaces and 0-2 IPv6 cluster interfaces.
        ipv4_interfaces = [
            self.make_ipv4_interface(nodegroup)
            for _ in range(random.randint(0, 2))
            ]
        ipv6_interfaces = [
            self.make_ipv6_interface(nodegroup)
            for _ in range(random.randint(0, 2))
            ]
        interfaces = sorted(
            ipv4_interfaces + ipv6_interfaces,
            key=lambda *args: random.randint(0, 10))

        ipv4_result, ipv6_result = split_ipv4_ipv6_interfaces(interfaces)

        self.assertItemsEqual(ipv4_interfaces, ipv4_result)
        self.assertItemsEqual(ipv6_interfaces, ipv6_result)


class TestMakeSubnetConfig(MAASServerTestCase):
    "Tests for `make_subnet_config`.""

    def test__includes_all_parameters(self):
        interface = factory.make_NodeGroupInterface(
            factory.make_NodeGroup())
        config = make_subnet_config(
            interface, factory.make_name('dns'), factory.make_name('ntp'))
        self.assertIsInstance(config, dict)
        self.assertThat(
            config.keys(),
            ContainsAll([
                'subnet',
                'subnet_mask',
                'subnet_cidr',
                'broadcast_ip',
                'interface',
                'router_ip',
                'dns_servers',
                'ntp_server',
                'domain_name',
                'ip_range_low',
                'ip_range_high',
                'hosts',
                ]))

    def test__sets_dns_and_ntp_from_arguments(self):
        interface = factory.make_NodeGroupInterface(
            factory.make_NodeGroup())
        dns = '%s %s' % (
            factory.make_ipv4_address(),
            factory.make_ipv6_address(),
            )
        ntp = factory.make_name('ntp')
        config = make_subnet_config(interface, dns_servers=dns, ntp_server=ntp)
        self.expectThat(config['dns_servers'], Equals(dns))
        self.expectThat(config['ntp_server'], Equals(ntp))

    def test__sets_domain_name_from_cluster(self):
        nodegroup = factory.make_NodeGroup()
        interface = factory.make_NodeGroupInterface(nodegroup)
        config = make_subnet_config(
            interface, factory.make_name('dns'), factory.make_name('ntp'))
        self.expectThat(config['domain_name'], Equals(nodegroup.name))

    def test__sets_other_items_from_interface(self):
        interface = factory.make_NodeGroupInterface(
            factory.make_NodeGroup())
        config = make_subnet_config(
            interface, factory.make_name('dns'), factory.make_name('ntp'))
        self.expectThat(config['broadcast_ip'], Equals(interface.broadcast_ip))
        self.expectThat(config['interface'], Equals(interface.interface))
        self.expectThat(config['router_ip'], Equals(interface.router_ip))

    def test__passes_IP_addresses_as_strings(self):
        interface = factory.make_NodeGroupInterface(
            factory.make_NodeGroup())
        config = make_subnet_config(
            interface, factory.make_name('dns'), factory.make_name('ntp'))
        self.expectThat(config['subnet'], IsInstance(str))
        self.expectThat(config['subnet_mask'], IsInstance(str))
        self.expectThat(config['subnet_cidr'], IsInstance(str))
        self.expectThat(config['broadcast_ip'], IsInstance(str))
        self.expectThat(config['router_ip'], IsInstance(str))
        self.expectThat(config['ip_range_low'], IsInstance(str))
        self.expectThat(config['ip_range_high'], IsInstance(str))

    def test__defines_IPv4_subnet(self):
        interface = factory.make_NodeGroupInterface(
            factory.make_NodeGroup(), network=IPNetwork('10.9.8.7/24'))
        config = make_subnet_config(
            interface, factory.make_name('dns'), factory.make_name('ntp'))
        self.expectThat(config['subnet'], Equals('10.9.8.0'))
        self.expectThat(config['subnet_mask'], Equals('255.255.255.0'))
        self.expectThat(config['subnet_cidr'], Equals('10.9.8.0/24'))

    def test__defines_IPv6_subnet(self):
        interface = factory.make_NodeGroupInterface(
            factory.make_NodeGroup(),
            network=IPNetwork('fd38:c341:27da:c831::/64'))
        config = make_subnet_config(
            interface, factory.make_name('dns'), factory.make_name('ntp'))
        # Don't expect a specific literal value, like we do for IPv4; there
        # are different spellings.
        self.expectThat(
            IPAddress(config['subnet']),
            Equals(IPAddress('fd38:c341:27da:c831::')))
        # (Netmask is not used for the IPv6 config, so ignore it.)
        self.expectThat(
            IPNetwork(config['subnet_cidr']),
            Equals(IPNetwork('fd38:c341:27da:c831::/64')))

    def test__passes_dynamic_range(self):
        interface = factory.make_NodeGroupInterface(
            factory.make_NodeGroup())
        config = make_subnet_config(
            interface, factory.make_name('dns'), factory.make_name('ntp'))
        self.expectThat(
            (config['ip_range_low'], config['ip_range_high']),
            Equals((interface.ip_range_low, interface.ip_range_high)))
        self.expectThat(
            config['ip_range_low'], Not(Equals(interface.static_ip_range_low)))

    def test__doesnt_convert_None_router_ip(self):
        interface = factory.make_NodeGroupInterface(factory.make_NodeGroup())
        interface.subnet.router_ip = None
        interface.save()
        post_commit_hooks.fire()
        config = make_subnet_config(
            interface, factory.make_name('dns'), factory.make_name('ntp'))
        self.assertEqual('', config['router_ip'])

    def tests__passes_defined_hosts(self):
        interface = factory.make_NodeGroupInterface(factory.make_NodeGroup())
        subnet = interface.subnet
        node = factory.make_Node(interface=False)

        # Make AUTO IP without an IP. Should not be in output.
        auto_no_ip_interface = factory.make_Interface(
            INTERFACE_TYPE.PHYSICAL, node=node, vlan=subnet.vlan)
        factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.AUTO, ip='', subnet=subnet,
            interface=auto_no_ip_interface)

        # Make AUTO IP with an IP. Should be in the output.
        auto_with_ip_interface = factory.make_Interface(
            INTERFACE_TYPE.PHYSICAL, node=node, vlan=subnet.vlan)
        auto_ip = factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.AUTO, subnet=subnet,
            interface=auto_with_ip_interface)

        # Make STICKY IP. Should be in the output.
        sticky_ip_interface = factory.make_Interface(
            INTERFACE_TYPE.PHYSICAL, node=node, vlan=subnet.vlan)
        sticky_ip = factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.STICKY, subnet=subnet,
            interface=sticky_ip_interface)

        # Make DISCOVERED IP. Should not be in the output.
        discovered_ip_interface = factory.make_Interface(
            INTERFACE_TYPE.PHYSICAL, node=node, vlan=subnet.vlan)
        factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.DISCOVERED, subnet=subnet,
            interface=discovered_ip_interface)

        # Make USER_RESERVED IP on Device. Should be in the output.
        device = factory.make_Device(interface=False)
        device_interface = factory.make_Interface(
            INTERFACE_TYPE.PHYSICAL, node=device, vlan=subnet.vlan)
        device_ip = factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.USER_RESERVED, subnet=subnet,
            interface=device_interface)

        # Make USER_RESERVED IP on Unknown interface. Should be in the output.
        unknown_interface = factory.make_Interface(
            INTERFACE_TYPE.UNKNOWN, vlan=subnet.vlan)
        unknown_reserved_ip = factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.USER_RESERVED, subnet=subnet,
            interface=unknown_interface)

        expected_hosts = sorted([
            {
                'host': '%s-%s' % (node.hostname, auto_with_ip_interface.name),
                'mac': str(auto_with_ip_interface.mac_address),
                'ip': str(auto_ip.ip),
            },
            {
                'host': '%s-%s' % (node.hostname, sticky_ip_interface.name),
                'mac': str(sticky_ip_interface.mac_address),
                'ip': str(sticky_ip.ip),
            },
            {
                'host': '%s-%s' % (device.hostname, device_interface.name),
                'mac': str(device_interface.mac_address),
                'ip': str(device_ip.ip),
            },
            {
                'host': 'unknown-%s-%s' % (
                    unknown_interface.id, unknown_interface.name),
                'mac': str(unknown_interface.mac_address),
                'ip': str(unknown_reserved_ip.ip),
            }
        ], key=itemgetter('host'))

        post_commit_hooks.fire()
        config = make_subnet_config(
            interface, factory.make_name('dns'), factory.make_name('ntp'))
        self.assertEqual(expected_hosts, config['hosts'])

    def tests__passes_defined_hosts_for_bond(self):
        interface = factory.make_NodeGroupInterface(factory.make_NodeGroup())
        subnet = interface.subnet
        node = factory.make_Node(interface=False)

        # Create a bond with an IP address, to make sure all MAC address in
        # that bond get the same address.
        eth0 = factory.make_Interface(
            INTERFACE_TYPE.PHYSICAL, node=node, name="eth0")
        eth1 = factory.make_Interface(
            INTERFACE_TYPE.PHYSICAL, node=node, name="eth1")
        eth2 = factory.make_Interface(
            INTERFACE_TYPE.PHYSICAL, node=node, name="eth2")
        bond0 = factory.make_Interface(
            INTERFACE_TYPE.BOND, node=node, name="bond0",
            mac_address=eth2.mac_address, parents=[eth0, eth1, eth2])
        auto_ip = factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.AUTO, subnet=subnet,
            interface=bond0)

        expected_hosts = [
            {
                'host': '%s-bond0' % node.hostname,
                'mac': str(bond0.mac_address),
                'ip': str(auto_ip.ip),
            },
            {
                'host': '%s-eth0' % node.hostname,
                'mac': str(eth0.mac_address),
                'ip': str(auto_ip.ip),
            },
            {
                'host': '%s-eth1' % node.hostname,
                'mac': str(eth1.mac_address),
                'ip': str(auto_ip.ip),
            },
        ]

        post_commit_hooks.fire()
        config = make_subnet_config(
            interface, factory.make_name('dns'), factory.make_name('ntp'))
        self.assertEqual(expected_hosts, config['hosts'])

    def tests__passes_defined_hosts_first_created_ip_address(self):
        interface = factory.make_NodeGroupInterface(factory.make_NodeGroup())
        subnet = interface.subnet
        node = factory.make_Node(interface=False)

        # Add two IP address to interface. Only the first should be added.
        eth0 = factory.make_Interface(
            INTERFACE_TYPE.PHYSICAL, node=node)
        auto_ip = factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.AUTO, subnet=subnet,
            interface=eth0)
        factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.AUTO, subnet=subnet,
            interface=eth0)

        expected_hosts = [
            {
                'host': '%s-%s' % (node.hostname, eth0.name),
                'mac': str(eth0.mac_address),
                'ip': str(auto_ip.ip),
            },
        ]

        post_commit_hooks.fire()
        config = make_subnet_config(
            interface, factory.make_name('dns'), factory.make_name('ntp'))
        self.assertEqual(expected_hosts, config['hosts'])


class TestDoConfigureDHCP(MAASServerTestCase):
    "Tests for `do_configure_dhcp`.""

    scenarios = (
        ("DHCPv4", {
            "command": ConfigureDHCPv4,
            "make_network": factory.make_ipv4_network,
            "make_address": factory.make_ipv4_address,
            "ip_version": 4,
        }),
        ("DHCPv6", {
            "command": ConfigureDHCPv6,
            "make_network": factory.make_ipv6_network,
            "make_address": factory.make_ipv6_address,
            "ip_version": 6,
        }),
    )

    def prepare_rpc(self, nodegroup):
        ""Set up test case for speaking RPC to `nodegroup`.

        :param nodegroup: A cluster.  It will "run" a mock RPC service.
        :return: Protocol, Command stub
        ""
        self.useFixture(RegionEventLoopFixture('rpc'))
        self.useFixture(RunningEventLoopFixture())
        fixture = self.useFixture(MockLiveRegionToClusterRPCFixture())
        cluster = fixture.makeCluster(nodegroup, self.command)
        return cluster, getattr(
            cluster, self.command.commandName.decode("ascii"))

    def test__configures_dhcp(self):
        dns_server = self.make_address()
        maas_url = compose_URL("http://", dns_server)
        nodegroup = factory.make_NodeGroup(
            status=NODEGROUP_STATUS.ENABLED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP,
            dhcp_key=factory.make_name('key'),
            network=self.make_network(),
            maas_url=maas_url)
        ntp_server = factory.make_name('ntp')

        protocol, command_stub = self.prepare_rpc(nodegroup)
        command_stub.side_effect = always_succeed_with({})

        # Although the above nodegroup has managed interfaces, we pass the
        # empty list here; do_configure_dhcp() dutifully believes us.
        do_configure_dhcp(
            self.ip_version, nodegroup, [], ntp_server,
            getClientFor(nodegroup.uuid))

        self.assertThat(
            command_stub, MockCalledOnceWith(
                ANY, omapi_key=nodegroup.dhcp_key, subnet_configs=[]))

    def test__configures_dhcp_with_subnets(self):
        dns_server = self.make_address()
        maas_url = compose_URL("http://", dns_server)
        nodegroup = factory.make_NodeGroup(
            status=NODEGROUP_STATUS.ENABLED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP,
            dhcp_key=factory.make_string(),
            interface=factory.make_name('eth'),
            network=self.make_network(),
            maas_url=maas_url)
        # Create a second DHCP-managed interface.
        factory.make_NodeGroupInterface(
            nodegroup=nodegroup, interface=factory.make_name('eth'),
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP,
            network=self.make_network())
        ntp_server = factory.make_name('ntp')
        interfaces = nodegroup.get_managed_interfaces()

        protocol, command_stub = self.prepare_rpc(nodegroup)
        command_stub.side_effect = always_succeed_with({})

        do_configure_dhcp(
            self.ip_version, nodegroup, interfaces, ntp_server,
            getClientFor(nodegroup.uuid))

        expected_subnet_configs = [
            make_subnet_config(interface, dns_server, ntp_server)
            for interface in nodegroup.get_managed_interfaces()
        ]

        self.assertThat(
            command_stub, MockCalledOnceWith(
                ANY, subnet_configs=expected_subnet_configs,
                omapi_key=nodegroup.dhcp_key,
            ))


class TestDoConfigureDHCPWrappers(MAASServerTestCase):
    "Tests for `do_configure_dhcp` wrapper functions.""

    def test_configure_dhcpv4_calls_do_configure_dhcp(self):
        do_configure_dhcp = self.patch_autospec(dhcp, "do_configure_dhcp")
        dhcp.configure_dhcpv4(
            sentinel.nodegroup, sentinel.interfaces, sentinel.ntp_server,
            sentinel.client)
        self.assertThat(do_configure_dhcp, MockCalledOnceWith(
            4, sentinel.nodegroup, sentinel.interfaces, sentinel.ntp_server,
            sentinel.client))

    def test_configure_dhcpv6_calls_do_configure_dhcp(self):
        do_configure_dhcp = self.patch_autospec(dhcp, "do_configure_dhcp")
        dhcp.configure_dhcpv6(
            sentinel.nodegroup, sentinel.interfaces, sentinel.ntp_server,
            sentinel.client)
        self.assertThat(do_configure_dhcp, MockCalledOnceWith(
            6, sentinel.nodegroup, sentinel.interfaces, sentinel.ntp_server,
            sentinel.client))


def patch_configure_funcs(test):
    "Patch `configure_dhcpv4` and `configure_dhcpv6`.""
    return (
        test.patch(dhcp, 'configure_dhcpv4'),
        test.patch(dhcp, 'configure_dhcpv6'),
    )


def make_cluster(test, status=None, omapi_key=None, **kwargs):
    "Create a `NodeGroup` without interfaces.

    Status defaults to `ACCEPTED`.
    ""
    if status is None:
        status = NODEGROUP_STATUS.ENABLED
    if omapi_key is None:
        # Set an arbitrary OMAPI key, so that the cluster won't need to
        # shell out to create one.
        omapi_key = factory.make_name('key')
    return factory.make_NodeGroup(
        status=status, dhcp_key=omapi_key, **kwargs)


def make_cluster_interface(
        test, network, cluster=None, management=None, **kwargs):
    if cluster is None:
        cluster = test.make_cluster()
    if management is None:
        management = NODEGROUPINTERFACE_MANAGEMENT.DHCP
    return factory.make_NodeGroupInterface(
        cluster, network=network, management=management, **kwargs)


def make_ipv4_interface(test, cluster=None, **kwargs):
    "Create an IPv4 `NodeGroupInterface` for `cluster`.

    The interface defaults to being managed.
    ""
    return make_cluster_interface(
        test, factory.make_ipv4_network(), cluster, **kwargs)


def make_ipv6_interface(test, cluster=None, **kwargs):
    ""Create an IPv6 `NodeGroupInterface` for `cluster`.

    The interface defaults to being managed.
    ""
    return make_cluster_interface(
        test, factory.make_ipv6_network(), cluster, **kwargs)


class TestConfigureDHCP(MAASServerTestCase):
    ""Tests for `configure_dhcp`.""

    def setUp(self):
        super(TestConfigureDHCP, self).setUp()
        # Suppress checks for cluster availability.
        self.patch_autospec(dhcp, "getClientFor")

    def test__obeys_DHCP_CONNECT(self):
        configure_dhcpv4, configure_dhcpv6 = patch_configure_funcs(self)
        cluster = make_cluster(self)
        make_ipv4_interface(self, cluster)
        make_ipv6_interface(self, cluster)
        self.patch(settings, "DHCP_CONNECT", False)

        with post_commit_hooks:
            configure_dhcp(cluster)

        self.expectThat(configure_dhcpv4, MockNotCalled())
        self.expectThat(configure_dhcpv6, MockNotCalled())

    def test__does_not_configure_interfaces_if_nodegroup_not_accepted(self):
        configure_dhcpv4, configure_dhcpv6 = patch_configure_funcs(self)
        cluster = make_cluster(self, status=NODEGROUP_STATUS.DISABLED)
        make_ipv4_interface(self, cluster)
        make_ipv6_interface(self, cluster)
        self.patch(settings, "DHCP_CONNECT", True)

        with post_commit_hooks:
            configure_dhcp(cluster)

        self.expectThat(configure_dhcpv4, MockCalledOnceWith(
            cluster, [], ANY, dhcp.getClientFor.return_value))
        self.expectThat(configure_dhcpv6, MockCalledOnceWith(
            cluster, [], ANY, dhcp.getClientFor.return_value))

    def test__configures_dhcpv4(self):
        ip = factory.make_ipv4_address()
        cluster = make_cluster(self, maas_url='http://%s/' % ip)
        make_ipv4_interface(self, cluster)
        self.patch(settings, "DHCP_CONNECT", True)

        with post_commit_hooks:
            configure_dhcp(cluster)

        self.assertThat(dhcp.getClientFor, MockCalledOnceWith(cluster.uuid))
        client = dhcp.getClientFor.return_value
        self.assertThat(client, MockCallsMatch(
            call(ANY, omapi_key=ANY, subnet_configs=ANY),
            call(ANY, omapi_key=ANY, subnet_configs=ANY),
        ))

        subnet_configs = [
            subnet_config
            for call_args in client.call_args_list
            for subnet_config in call_args[1]['subnet_configs']
        ]
        self.assertThat(
            subnet_configs, AllMatch(
                ContainsDict({"dns_servers": Equals(ip)})))

    def test__uses_ntp_server_from_config(self):
        configure_dhcpv4, configure_dhcpv6 = patch_configure_funcs(self)
        cluster = make_cluster(self)
        make_ipv4_interface(self, cluster)
        self.patch(settings, "DHCP_CONNECT", True)

        with post_commit_hooks:
            configure_dhcp(cluster)

        ntp_server = Config.objects.get_config('ntp_server')
        self.assertThat(
            configure_dhcpv4, MockCalledOnceWith(
                ANY, ANY, ntp_server, dhcp.getClientFor.return_value))
        self.assertThat(
            configure_dhcpv6, MockCalledOnceWith(
                ANY, ANY, ntp_server, dhcp.getClientFor.return_value))


class TestConfigureDHCPWithDisconnectedCluster(MAASServerTestCase):
    ""Behaviour when the target cluster is not connected.""

    def test__logs_about_disconnected_cluster(self):
        cluster = make_cluster(self)
        self.patch(settings, "DHCP_CONNECT", True)

        with LoggerFixture(dhcp.__name__) as logger:
            with post_commit_hooks:
                configure_dhcp(cluster)

        self.assertDocTestMatches(
            ""\
            Cluster ... (...) is not connected at present so cannot be
            configured; it will catch up when it next connects.
            "",
            logger.output)


class TestConfigureDHCPTransactional(MAASTransactionServerTestCase):
    ""Tests for `configure_dhcp` that require transactions.

    Specifically, post-commit hooks are run in a separate thread, so changes
    must be committed to the database in order that they're visible elsewhere.
    ""

    def setUp(self):
        super(TestConfigureDHCPTransactional, self).setUp()
        # Suppress checks for cluster availability.
        getClientFor = self.patch_autospec(dhcp, "getClientFor")
        getClientFor.return_value = sentinel.client
        # Connect DHCP changes.
        self.patch(settings, "DHCP_CONNECT", True)

    def test__passes_only_IPv4_interfaces_to_DHCPv4(self):
        configure_dhcpv4, _ = patch_configure_funcs(self)

        with transaction.atomic():
            cluster = make_cluster(self)
            ipv4_interface = make_ipv4_interface(self, cluster)
            make_ipv6_interface(self, cluster)

        with post_commit_hooks:
            configure_dhcp(cluster)

        self.assertThat(configure_dhcpv4, MockCalledOnceWith(
            cluster, [ipv4_interface], ANY, sentinel.client))

    def test__passes_only_IPv6_interfaces_to_DHCPv6(self):
        _, configure_dhcpv6 = patch_configure_funcs(self)

        with transaction.atomic():
            cluster = make_cluster(self)
            ipv6_interface = make_ipv6_interface(self, cluster)
            make_ipv4_interface(self, cluster)

        with post_commit_hooks:
            configure_dhcp(cluster)

        self.assertThat(configure_dhcpv6, MockCalledOnceWith(
            cluster, [ipv6_interface], ANY, sentinel.client))


class TestDHCPConnect(MAASServerTestCase):
    ""Tests for DHCP signals triggered when saving a cluster interface.""

    def setUp(self):
        super(TestDHCPConnect, self).setUp()
        self.patch_autospec(dhcp, "configure_dhcp")

    def test_dhcp_config_gets_written_when_nodegroup_becomes_active(self):
        nodegroup = factory.make_NodeGroup(
            status=NODEGROUP_STATUS.DISABLED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP)
        self.patch(settings, "DHCP_CONNECT", True)

        nodegroup.accept()

        self.assertThat(dhcp.configure_dhcp, MockCalledOnceWith(nodegroup))

    def test_dhcp_config_gets_written_when_nodegroup_name_changes(self):
        nodegroup = factory.make_NodeGroup(
            status=NODEGROUP_STATUS.ENABLED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP)
        self.patch(settings, "DHCP_CONNECT", True)

        nodegroup.name = factory.make_name('domain')
        nodegroup.save()

        self.assertThat(dhcp.configure_dhcp, MockCalledOnceWith(nodegroup))

    def test_dhcp_config_gets_written_when_interface_IP_changes(self):
        nodegroup = factory.make_NodeGroup(
            status=NODEGROUP_STATUS.ENABLED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP)
        [interface] = nodegroup.nodegroupinterface_set.all()
        self.patch(settings, "DHCP_CONNECT", True)

        interface.ip = factory.pick_ip_in_network(
            interface.network, but_not=[interface.ip])
        interface.save()

        self.assertThat(dhcp.configure_dhcp, MockCalledOnceWith(nodegroup))

    def test_dhcp_config_gets_written_when_interface_management_changes(self):
        nodegroup = factory.make_NodeGroup(
            status=NODEGROUP_STATUS.ENABLED,
            management=NODEGROUPINTERFACE_MANAGEMENT.UNMANAGED)
        [interface] = nodegroup.nodegroupinterface_set.all()
        self.patch(settings, "DHCP_CONNECT", True)

        interface.management = NODEGROUPINTERFACE_MANAGEMENT.DHCP
        interface.save()

        self.assertThat(dhcp.configure_dhcp, MockCalledOnceWith(nodegroup))

    def test_dhcp_config_gets_written_when_interface_name_changes(self):
        nodegroup = factory.make_NodeGroup(
            status=NODEGROUP_STATUS.ENABLED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP)
        [interface] = nodegroup.get_managed_interfaces()
        self.patch(settings, "DHCP_CONNECT", True)

        interface.interface = factory.make_name('itf')
        interface.save()

        self.assertThat(dhcp.configure_dhcp, MockCalledOnceWith(nodegroup))

    def test_dhcp_config_gets_written_when_netmask_changes(self):
        network = factory.make_ipv4_network(slash='255.255.255.0')
        subnet = factory.make_Subnet(cidr=str(network.cidr))
        nodegroup = factory.make_NodeGroup(
            status=NODEGROUP_STATUS.ENABLED, subnet=subnet,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP)
        [interface] = nodegroup.get_managed_interfaces()
        self.patch(settings, "DHCP_CONNECT", True)

        interface.subnet_mask = '255.255.0.0'
        interface.save()

        self.assertThat(dhcp.configure_dhcp, MockCalledOnceWith(nodegroup))

    def test_dhcp_config_gets_written_when_interface_router_ip_changes(self):
        nodegroup = factory.make_NodeGroup(
            status=NODEGROUP_STATUS.ENABLED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP)
        [interface] = nodegroup.get_managed_interfaces()
        self.patch(settings, "DHCP_CONNECT", True)

        interface.subnet.gateway_ip = factory.pick_ip_in_network(
            interface.network, but_not=[interface.subnet.gateway_ip])
        interface.save()

        self.assertThat(dhcp.configure_dhcp, MockCalledOnceWith(nodegroup))

    def test_dhcp_config_gets_written_when_ip_range_changes(self):
        nodegroup = factory.make_NodeGroup(
            status=NODEGROUP_STATUS.ENABLED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP)
        [interface] = nodegroup.get_managed_interfaces()
        self.patch(settings, "DHCP_CONNECT", True)

        interface.ip_range_low = str(
            IPAddress(interface.ip_range_low) + 1)
        interface.ip_range_high = str(
            IPAddress(interface.ip_range_high) - 1)
        interface.save()

        self.assertThat(dhcp.configure_dhcp, MockCalledOnceWith(nodegroup))

    def test_dhcp_config_is_not_written_when_foreign_dhcp_changes(self):
        nodegroup = factory.make_NodeGroup(
            status=NODEGROUP_STATUS.ENABLED,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP)
        [interface] = nodegroup.get_managed_interfaces()
        self.patch(settings, "DHCP_CONNECT", True)

        interface.foreign_dhcp = factory.pick_ip_in_network(interface.network)
        interface.save()

        self.assertThat(dhcp.configure_dhcp, MockNotCalled())

    def test_dhcp_config_gets_written_when_ntp_server_changes(self):
        # When the "ntp_server" Config item is changed, check that all
        # nodegroups get their DHCP config re-written.

        # XXX 2015-09-17 blake_r: Isolation issue where an extra NodeGroup
        # already exists. So we remove all the nodegroup's before performing
        # this test.
        NodeGroup.objects.all().delete()

        num_active_nodegroups = random.randint(1, 10)
        num_inactive_nodegroups = random.randint(1, 10)
        for _ in range(num_active_nodegroups):
            factory.make_NodeGroup(
                status=NODEGROUP_STATUS.ENABLED,
                management=NODEGROUPINTERFACE_MANAGEMENT.DHCP)
        for _ in range(num_inactive_nodegroups):
            factory.make_NodeGroup(
                status=NODEGROUP_STATUS.DISABLED,
                management=NODEGROUPINTERFACE_MANAGEMENT.DHCP)
        self.patch(settings, "DHCP_CONNECT", True)

        Config.objects.set_config("ntp_server", factory.make_ipv4_address())

        # Every nodegroup is updated, including those that are PENDING.
        expected_call_one_nodegroup = [call(ANY)]
        expected_calls = expected_call_one_nodegroup * (
            num_active_nodegroups + num_inactive_nodegroups)
        self.assertThat(dhcp.configure_dhcp, MockCallsMatch(*expected_calls))

    def test_dhcp_config_gets_written_when_managed_interface_is_deleted(self):
        interface = factory.make_NodeGroupInterface(
            factory.make_NodeGroup(status=NODEGROUP_STATUS.ENABLED),
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP)
        self.patch(settings, "DHCP_CONNECT", True)

        interface.delete()

        self.assertThat(
            dhcp.configure_dhcp, MockCalledOnceWith(interface.nodegroup))


# Matchers to check that a `Changes` object is empty, or not.
changes_are_empty = MatchesStructure.byEquality(hook=None, clusters=[])
changes_are_not_empty = Not(changes_are_empty)


class TestConsolidatingChangesWhenDisconnected(MAASServerTestCase):
    ""Tests for `Changes` and `ChangeConsolidator` when disconnected.

    Where "disconnected" means where `settings.DHCP_CONNECT` is `False`.
    ""

    def test__does_nothing(self):
        self.patch(settings, "DHCP_CONNECT", False)
        consolidator.configure(sentinel.cluster)
        self.assertThat(consolidator.changes, changes_are_empty)


class TestConsolidatingChanges(MAASServerTestCase):
    ""Tests for `Changes` and `ChangeConsolidator`.""

    def setUp(self):
        super(TestConsolidatingChanges, self).setUp()
        self.patch(settings, "DHCP_CONNECT", True)

    def test__added_clusters_applied_post_commit(self):
        configure_dhcp_now = self.patch_autospec(dhcp, "configure_dhcp_now")
        cluster = make_cluster(self)
        consolidator.configure(cluster)
        self.assertThat(configure_dhcp_now, MockNotCalled())
        post_commit_hooks.fire()
        self.assertThat(configure_dhcp_now, MockCalledOnceWith(cluster))

    def test__added_clusters_are_consolidated(self):
        configure_dhcp_now = self.patch_autospec(dhcp, "configure_dhcp_now")
        cluster = make_cluster(self)
        consolidator.configure(cluster)
        consolidator.configure(cluster)
        post_commit_hooks.fire()
        self.assertThat(configure_dhcp_now, MockCalledOnceWith(cluster))

    def test__changes_are_reset_post_commit(self):
        self.patch_autospec(dhcp, "configure_dhcp_now")

        # The changes start empty.
        self.assertThat(consolidator.changes, changes_are_empty)

        cluster = make_cluster(self)
        consolidator.configure(cluster)

        # The changes are not empty now.
        self.assertThat(consolidator.changes, changes_are_not_empty)

        # They are once again empty after the post-commit hook fires.
        post_commit_hooks.fire()
        self.assertThat(consolidator.changes, changes_are_empty)

    def test__changes_are_reset_post_commit_on_failure(self):
        exception_type = factory.make_exception_type()

        configure_dhcp_now = self.patch_autospec(dhcp, "configure_dhcp_now")
        configure_dhcp_now.side_effect = exception_type

        # This is going to crash later.
        consolidator.configure(make_cluster(self))

        # The changes are empty after the post-commit hook fires.
        self.assertRaises(exception_type, post_commit_hooks.fire)
        self.assertThat(consolidator.changes, changes_are_empty)
"""
