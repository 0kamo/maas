# Copyright 2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for Subnet API."""

__all__ = []

import http.client
import json
import random

from django.conf import settings
from django.core.urlresolvers import reverse
from maasserver.enum import (
    IPADDRESS_TYPE,
    IPRANGE_TYPE,
    NODE_STATUS,
    RDNS_MODE_CHOICES,
)
from maasserver.testing.api import (
    APITestCase,
    explain_unexpected_response,
)
from maasserver.testing.factory import factory
from maasserver.testing.orm import reload_object
from maasserver.testing.testcase import MAASServerTestCase
from provisioningserver.utils.network import (
    inet_ntop,
    IPRangeStatistics,
)
from testtools.matchers import (
    ContainsDict,
    Equals,
)


def get_subnets_uri():
    """Return a Subnet's URI on the API."""
    return reverse('subnets_handler', args=[])


def get_subnet_uri(subnet):
    """Return a Subnet URI on the API."""
    if isinstance(subnet, str):
        return reverse(
            'subnet_handler', args=[subnet])
    else:
        return reverse(
            'subnet_handler', args=[subnet.id])


class TestSubnetsAPI(APITestCase):

    def test_handler_path(self):
        self.assertEqual(
            '/api/2.0/subnets/', get_subnets_uri())

    def test_read(self):
        subnets = [
            factory.make_Subnet()
            for _ in range(3)
        ]
        uri = get_subnets_uri()
        response = self.client.get(uri)

        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        expected_ids = [
            subnet.id
            for subnet in subnets
            ]
        result_ids = [
            subnet["id"]
            for subnet in json.loads(
                response.content.decode(settings.DEFAULT_CHARSET))
            ]
        self.assertItemsEqual(expected_ids, result_ids)

    def test_create(self):
        self.become_admin()
        subnet_name = factory.make_name("subnet")
        vlan = factory.make_VLAN()
        space = factory.make_Space()
        network = factory.make_ip4_or_6_network()
        cidr = str(network.cidr)
        rdns_mode = factory.pick_choice(RDNS_MODE_CHOICES)
        gateway_ip = factory.pick_ip_in_network(network)
        dns_servers = []
        for _ in range(2):
            dns_servers.append(
                factory.pick_ip_in_network(
                    network, but_not=[gateway_ip] + dns_servers))
        uri = get_subnets_uri()
        response = self.client.post(uri, {
            "name": subnet_name,
            "vlan": vlan.id,
            "space": space.id,
            "cidr": cidr,
            "gateway_ip": gateway_ip,
            "dns_servers": ','.join(dns_servers),
            "rdns_mode": rdns_mode,
        })
        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        created_subnet = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertEqual(subnet_name, created_subnet['name'])
        self.assertEqual(vlan.vid, created_subnet['vlan']['vid'])
        self.assertEqual(space.get_name(), created_subnet['space'])
        self.assertEqual(cidr, created_subnet['cidr'])
        self.assertEqual(gateway_ip, created_subnet['gateway_ip'])
        self.assertEqual(dns_servers, created_subnet['dns_servers'])
        self.assertEqual(rdns_mode, created_subnet['rdns_mode'])

    def test_create_admin_only(self):
        subnet_name = factory.make_name("subnet")
        uri = get_subnets_uri()
        response = self.client.post(uri, {
            "name": subnet_name,
        })
        self.assertEqual(
            http.client.FORBIDDEN, response.status_code, response.content)

    def test_create_requires_name_vlan_space_cidr(self):
        self.become_admin()
        uri = get_subnets_uri()
        response = self.client.post(uri, {})
        self.assertEqual(
            http.client.BAD_REQUEST, response.status_code, response.content)
        self.assertEqual({
            "cidr": ["This field is required."],
            }, json.loads(response.content.decode(settings.DEFAULT_CHARSET)))


class TestSubnetAPI(APITestCase):

    def test_handler_path(self):
        subnet = factory.make_Subnet()
        self.assertEqual(
            '/api/2.0/subnets/%s/' % subnet.id,
            get_subnet_uri(subnet))

    def test_read(self):
        subnet = factory.make_Subnet()
        uri = get_subnet_uri(subnet)
        response = self.client.get(uri)

        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        parsed_subnet = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertThat(parsed_subnet, ContainsDict({
            "id": Equals(subnet.id),
            "name": Equals(subnet.name),
            "vlan": ContainsDict({
                "vid": Equals(subnet.vlan.vid),
                }),
            "space": Equals(subnet.space.get_name()),
            "cidr": Equals(subnet.cidr),
            "gateway_ip": Equals(subnet.gateway_ip),
            "dns_servers": Equals(subnet.dns_servers),
            }))

    def test_read_404_when_bad_id(self):
        uri = reverse(
            'subnet_handler', args=[random.randint(100, 1000)])
        response = self.client.get(uri)
        self.assertEqual(
            http.client.NOT_FOUND, response.status_code, response.content)

    def test_read_400_when_blank_id(self):
        uri = reverse(
            'subnet_handler', args=[" "])
        response = self.client.get(uri)
        self.assertEqual(
            http.client.BAD_REQUEST, response.status_code, response.content)

    def test_read_403_when_ambiguous(self):
        fabric = factory.make_Fabric(name="foo")
        factory.make_Subnet(fabric=fabric)
        factory.make_Subnet(fabric=fabric)
        uri = reverse(
            'subnet_handler', args=["fabric:foo"])
        response = self.client.get(uri)
        self.assertEqual(
            http.client.FORBIDDEN, response.status_code, response.content)

    def test_update(self):
        self.become_admin()
        subnet = factory.make_Subnet()
        new_name = factory.make_name("subnet")
        new_rdns_mode = factory.pick_choice(RDNS_MODE_CHOICES)
        uri = get_subnet_uri(subnet)
        response = self.client.put(uri, {
            "name": new_name,
            "rdns_mode": new_rdns_mode,
        })
        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        self.assertEqual(
            new_name, json.loads(
                response.content.decode(settings.DEFAULT_CHARSET))['name'])
        self.assertEqual(new_name, reload_object(subnet).name)
        self.assertEqual(new_rdns_mode, reload_object(subnet).rdns_mode)

    def test_update_admin_only(self):
        subnet = factory.make_Subnet()
        new_name = factory.make_name("subnet")
        uri = get_subnet_uri(subnet)
        response = self.client.put(uri, {
            "name": new_name,
        })
        self.assertEqual(
            http.client.FORBIDDEN, response.status_code, response.content)

    def test_delete_deletes_subnet(self):
        self.become_admin()
        subnet = factory.make_Subnet()
        uri = get_subnet_uri(subnet)
        response = self.client.delete(uri)
        self.assertEqual(
            http.client.NO_CONTENT, response.status_code, response.content)
        self.assertIsNone(reload_object(subnet))

    def test_delete_deletes_subnet_by_name(self):
        self.become_admin()
        subnet = factory.make_Subnet(name=factory.make_name('subnet'))
        uri = get_subnet_uri("name:%s" % subnet.name)
        response = self.client.delete(uri)
        self.assertEqual(
            http.client.NO_CONTENT, response.status_code, response.content)
        self.assertIsNone(reload_object(subnet))

    def test_delete_deletes_subnet_by_cidr(self):
        self.become_admin()
        subnet = factory.make_Subnet(name=factory.make_name('subnet'))
        uri = get_subnet_uri("cidr:%s" % subnet.cidr)
        response = self.client.delete(uri)
        self.assertEqual(
            http.client.NO_CONTENT, response.status_code, response.content)
        self.assertIsNone(reload_object(subnet))

    def test_delete_403_when_not_admin(self):
        subnet = factory.make_Subnet()
        uri = get_subnet_uri(subnet)
        response = self.client.delete(uri)
        self.assertEqual(
            http.client.FORBIDDEN, response.status_code, response.content)
        self.assertIsNotNone(reload_object(subnet))

    def test_delete_404_when_invalid_id(self):
        self.become_admin()
        uri = reverse(
            'subnet_handler', args=[random.randint(100, 1000)])
        response = self.client.delete(uri)
        self.assertEqual(
            http.client.NOT_FOUND, response.status_code, response.content)


class TestSubnetAPIAuth(MAASServerTestCase):
    """Authorization tests for subnet API."""
    def test__reserved_ip_ranges_fails_if_not_logged_in(self):
        subnet = factory.make_Subnet()
        response = self.client.get(
            get_subnet_uri(subnet),
            {'op': 'reserved_ip_ranges'})
        self.assertEqual(
            http.client.UNAUTHORIZED, response.status_code,
            explain_unexpected_response(http.client.UNAUTHORIZED, response))

    def test__unreserved_ip_ranges_fails_if_not_logged_in(self):
        subnet = factory.make_Subnet()
        response = self.client.get(
            get_subnet_uri(subnet),
            {'op': 'unreserved_ip_ranges'})
        self.assertEqual(
            http.client.UNAUTHORIZED, response.status_code,
            explain_unexpected_response(http.client.UNAUTHORIZED, response))


class TestSubnetReservedIPRangesAPI(APITestCase):

    def test__returns_empty_list_for_empty_subnet(self):
        subnet = factory.make_Subnet()
        response = self.client.get(
            get_subnet_uri(subnet),
            {'op': 'reserved_ip_ranges'})
        self.assertEqual(
            http.client.OK, response.status_code,
            explain_unexpected_response(http.client.OK, response))
        result = json.loads(response.content.decode(settings.DEFAULT_CHARSET))
        self.assertThat(result, Equals([]))

    def test__accounts_for_reserved_ip_address(self):
        subnet = factory.make_Subnet()
        ip = factory.pick_ip_in_network(subnet.get_ipnetwork())
        factory.make_StaticIPAddress(
            ip=ip, alloc_type=IPADDRESS_TYPE.AUTO, subnet=subnet)
        response = self.client.get(
            get_subnet_uri(subnet),
            {'op': 'reserved_ip_ranges'})
        self.assertEqual(
            http.client.OK, response.status_code,
            explain_unexpected_response(http.client.OK, response))
        result = json.loads(response.content.decode(settings.DEFAULT_CHARSET))
        self.assertThat(result, Equals([
            {
                "start": ip,
                "end": ip,
                "purpose": ["assigned-ip"],
                "num_addresses": 1,
            }]))


class TestSubnetUnreservedIPRangesAPI(APITestCase):

    def test__returns_full_list_for_empty_subnet(self):
        subnet = factory.make_Subnet()
        network = subnet.get_ipnetwork()
        response = self.client.get(
            get_subnet_uri(subnet),
            {'op': 'unreserved_ip_ranges'})
        self.assertEqual(
            http.client.OK, response.status_code,
            explain_unexpected_response(http.client.OK, response))
        result = json.loads(response.content.decode(settings.DEFAULT_CHARSET))
        expected_addresses = (network.last - network.first + 1)
        expected_first_address = inet_ntop(network.first + 1)
        if network.version == 6:
            # Don't count the IPv6 network address in num_addresses
            expected_addresses -= 1
            expected_last_address = inet_ntop(network.last)
        else:
            # Don't count the IPv4 broadcast/network addresses in num_addresses
            expected_addresses -= 2
            expected_last_address = inet_ntop(network.last - 1)
        self.assertThat(result, Equals([
            {
                "start": expected_first_address,
                "end": expected_last_address,
                "num_addresses": expected_addresses,
            }]))

    def test__returns_empty_list_for_full_subnet(self):
        subnet = factory.make_Subnet()
        network = subnet.get_ipnetwork()
        first_address = inet_ntop(network.first + 1)
        if network.version == 6:
            last_address = inet_ntop(network.last)
        else:
            last_address = inet_ntop(network.last - 1)
        factory.make_IPRange(
            subnet, first_address, last_address,
            type=IPRANGE_TYPE.DYNAMIC)
        response = self.client.get(
            get_subnet_uri(subnet),
            {'op': 'unreserved_ip_ranges'})
        result = json.loads(response.content.decode(settings.DEFAULT_CHARSET))
        self.assertEqual(
            http.client.OK, response.status_code,
            explain_unexpected_response(http.client.OK, response))
        self.assertThat(
            result, Equals([]), str(subnet.get_ipranges_in_use()))

    def test__accounts_for_reserved_ip_address(self):
        subnet = factory.make_Subnet()
        network = subnet.get_ipnetwork()
        # Pick an address in the middle of the range. (that way we'll always
        # expect there to be two unreserved ranges, arranged around the
        # allocated IP address.)
        middle_ip = (network.first + network.last) // 2
        ip = inet_ntop(middle_ip)
        factory.make_StaticIPAddress(
            ip=ip, alloc_type=IPADDRESS_TYPE.AUTO, subnet=subnet)

        expected_addresses = (network.last - network.first + 1)
        expected_first_address = inet_ntop(network.first + 1)
        first_range_end = inet_ntop(middle_ip - 1)
        first_range_size = middle_ip - network.first - 1
        second_range_start = inet_ntop(middle_ip + 1)
        if network.version == 6:
            # Don't count the IPv6 network address in num_addresses
            expected_addresses -= 1
            expected_last_address = inet_ntop(network.last)
            second_range_size = network.last - middle_ip
        else:
            # Don't count the IPv4 broadcast/network addresses in num_addresses
            expected_addresses -= 2
            expected_last_address = inet_ntop(network.last - 1)
            second_range_size = network.last - middle_ip - 1

        response = self.client.get(
            get_subnet_uri(subnet),
            {'op': 'unreserved_ip_ranges'})
        self.assertEqual(
            http.client.OK, response.status_code,
            explain_unexpected_response(http.client.OK, response))
        result = json.loads(response.content.decode(settings.DEFAULT_CHARSET))
        self.assertThat(result, Equals([
            {
                "start": expected_first_address,
                "end": first_range_end,
                "num_addresses": first_range_size,
            },
            {
                "start": second_range_start,
                "end": expected_last_address,
                "num_addresses": second_range_size,
            }]))


class TestSubnetStatisticsAPI(APITestCase):

    def test__default_does_not_include_ranges(self):
        subnet = factory.make_Subnet()
        factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.USER_RESERVED, subnet=subnet)
        response = self.client.get(
            get_subnet_uri(subnet), {
                'op': 'statistics',
            })
        self.assertEqual(
            http.client.OK, response.status_code,
            explain_unexpected_response(http.client.OK, response))
        result = json.loads(response.content.decode(settings.DEFAULT_CHARSET))
        full_iprange = subnet.get_iprange_usage()
        statistics = IPRangeStatistics(full_iprange)
        expected_result = statistics.render_json(include_ranges=False)
        self.assertThat(result, Equals(expected_result))

    def test__with_include_ranges(self):
        subnet = factory.make_Subnet()
        factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.USER_RESERVED, subnet=subnet)
        response = self.client.get(
            get_subnet_uri(subnet), {
                'op': 'statistics',
                'include_ranges': 'true'
            })
        self.assertEqual(
            http.client.OK, response.status_code,
            explain_unexpected_response(http.client.OK, response))
        result = json.loads(response.content.decode(settings.DEFAULT_CHARSET))
        full_iprange = subnet.get_iprange_usage()
        statistics = IPRangeStatistics(full_iprange)
        expected_result = statistics.render_json(include_ranges=True)
        self.assertThat(result, Equals(expected_result))

    def test__without_include_ranges(self):
        subnet = factory.make_Subnet()
        factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.USER_RESERVED, subnet=subnet)
        response = self.client.get(
            get_subnet_uri(subnet), {
                'op': 'statistics',
                'include_ranges': 'false'
            })
        self.assertEqual(
            http.client.OK, response.status_code,
            explain_unexpected_response(http.client.OK, response))
        result = json.loads(response.content.decode(settings.DEFAULT_CHARSET))
        full_iprange = subnet.get_iprange_usage()
        statistics = IPRangeStatistics(full_iprange)
        expected_result = statistics.render_json(include_ranges=False)
        self.assertThat(result, Equals(expected_result))


class TestSubnetIPAddressesAPI(APITestCase):

    def test__default_parameters(self):
        subnet = factory.make_Subnet()
        user = factory.make_User()
        node = factory.make_Node_with_Interface_on_Subnet(
            subnet=subnet, status=NODE_STATUS.READY)
        factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.USER_RESERVED, subnet=subnet, user=user)
        factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.STICKY, subnet=subnet, user=user,
            interface=node.get_boot_interface())
        response = self.client.get(
            get_subnet_uri(subnet), {
                'op': 'ip_addresses',
            })
        self.assertEqual(
            http.client.OK, response.status_code,
            explain_unexpected_response(http.client.OK, response))
        result = json.loads(response.content.decode(settings.DEFAULT_CHARSET))
        expected_result = subnet.render_json_for_related_ips(
            with_username=True, with_node_summary=True)
        self.assertThat(result, Equals(expected_result))

    def test__with_username_false(self):
        subnet = factory.make_Subnet()
        user = factory.make_User()
        node = factory.make_Node_with_Interface_on_Subnet(
            subnet=subnet, status=NODE_STATUS.READY)
        factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.USER_RESERVED, subnet=subnet, user=user)
        factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.STICKY, subnet=subnet, user=user,
            interface=node.get_boot_interface())
        response = self.client.get(
            get_subnet_uri(subnet), {
                'op': 'ip_addresses',
                'with_username': 'false',
            })
        self.assertEqual(
            http.client.OK, response.status_code,
            explain_unexpected_response(http.client.OK, response))
        result = json.loads(response.content.decode(settings.DEFAULT_CHARSET))
        expected_result = subnet.render_json_for_related_ips(
            with_username=False, with_node_summary=True)
        self.assertThat(result, Equals(expected_result))

    def test__with_node_summary_false(self):
        subnet = factory.make_Subnet()
        user = factory.make_User()
        node = factory.make_Node_with_Interface_on_Subnet(
            subnet=subnet, status=NODE_STATUS.READY)
        factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.USER_RESERVED, subnet=subnet, user=user)
        factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.STICKY, subnet=subnet, user=user,
            interface=node.get_boot_interface())
        response = self.client.get(
            get_subnet_uri(subnet), {
                'op': 'ip_addresses',
                'with_node_summary': 'false',
            })
        self.assertEqual(
            http.client.OK, response.status_code,
            explain_unexpected_response(http.client.OK, response))
        result = json.loads(response.content.decode(settings.DEFAULT_CHARSET))
        expected_result = subnet.render_json_for_related_ips(
            with_username=True, with_node_summary=False)
        self.assertThat(result, Equals(expected_result))
