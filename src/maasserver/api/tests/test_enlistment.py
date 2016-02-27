# Copyright 2013-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for enlistment-related portions of the API."""

__all__ = []

import http.client
import json

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.urlresolvers import reverse
from maasserver.enum import (
    INTERFACE_TYPE,
    NODE_STATUS,
)
from maasserver.fields import MAC
from maasserver.models import (
    Domain,
    Machine,
    Node,
)
from maasserver.models.node import PowerInfo
from maasserver.testing.api import MultipleUsersScenarios
from maasserver.testing.architecture import make_usable_architecture
from maasserver.testing.factory import factory
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.utils import strip_domain
from maasserver.utils.converters import json_load_bytes
from maasserver.utils.orm import (
    get_one,
    reload_object,
)


class EnlistmentAPITest(MultipleUsersScenarios,
                        MAASServerTestCase):
    """Enlistment tests."""
    scenarios = [
        ('anon', dict(userfactory=lambda: AnonymousUser())),
        ('user', dict(userfactory=factory.make_User)),
        ('admin', dict(userfactory=factory.make_admin)),
        ]

    def setUp(self):
        super().setUp()
        self.patch(Node, 'get_effective_power_info').return_value = (
            PowerInfo(False, False, False, None, None))

    def test_POST_create_creates_machine(self):
        architecture = make_usable_architecture(self)
        response = self.client.post(
            reverse('machines_handler'),
            {
                'hostname': 'diane',
                'architecture': architecture,
                'power_type': 'ether_wake',
                'mac_addresses': ['aa:bb:cc:dd:ee:ff', '22:bb:cc:dd:ee:ff'],
            })

        self.assertEqual(http.client.OK, response.status_code)
        parsed_result = json_load_bytes(response.content)
        self.assertIn('application/json', response['Content-Type'])
        domain_name = Domain.objects.get_default_domain().name
        self.assertEqual(
            'diane.%s' % domain_name, parsed_result['hostname'])
        self.assertNotEqual(0, len(parsed_result.get('system_id')))
        [diane] = Machine.objects.filter(hostname='diane')
        self.assertEqual(architecture, diane.architecture)

    def test_POST_new_generates_hostname_if_ip_based_hostname(self):
        Domain.objects.get_or_create(name="domain")
        hostname = '192-168-5-19.domain'
        response = self.client.post(
            reverse('machines_handler'),
            {
                'hostname': hostname,
                'architecture': make_usable_architecture(self),
                'power_type': 'ether_wake',
                'mac_addresses': [factory.make_mac_address()],
            })
        self.assertEqual(http.client.OK, response.status_code)
        parsed_result = json_load_bytes(response.content)

        system_id = parsed_result.get('system_id')
        machine = Machine.objects.get(system_id=system_id)
        self.assertNotEqual(hostname, machine.hostname)

    def test_POST_create_creates_machine_with_power_parameters(self):
        # We're setting power parameters so we disable start_commissioning to
        # prevent anything from attempting to issue power instructions.
        self.patch(Node, "start_commissioning")
        hostname = factory.make_name("hostname")
        architecture = make_usable_architecture(self)
        power_type = 'ipmi'
        power_parameters = {
            "power_user": factory.make_name("power-user"),
            "power_pass": factory.make_name("power-pass"),
            }
        response = self.client.post(
            reverse('machines_handler'),
            {
                'hostname': hostname,
                'architecture': architecture,
                'power_type': 'ether_wake',
                'mac_addresses': factory.make_mac_address(),
                'power_parameters': json.dumps(power_parameters),
                'power_type': power_type,
            })
        self.assertEqual(http.client.OK, response.status_code)
        [machine] = Machine.objects.filter(hostname=hostname)
        self.assertEqual(power_parameters, machine.power_parameters)
        self.assertEqual(power_type, machine.power_type)

    def test_POST_create_creates_machine_with_arch_only(self):
        architecture = make_usable_architecture(self, subarch_name="generic")
        response = self.client.post(
            reverse('machines_handler'),
            {
                'hostname': 'diane',
                'architecture': architecture.split('/')[0],
                'power_type': 'ether_wake',
                'mac_addresses': ['aa:bb:cc:dd:ee:ff', '22:bb:cc:dd:ee:ff'],
            })

        self.assertEqual(http.client.OK, response.status_code)
        parsed_result = json_load_bytes(response.content)
        self.assertIn('application/json', response['Content-Type'])
        domain_name = Domain.objects.get_default_domain().name
        self.assertEqual(
            'diane.%s' % domain_name, parsed_result['hostname'])
        self.assertNotEqual(0, len(parsed_result.get('system_id')))
        [diane] = Machine.objects.filter(hostname='diane')
        self.assertEqual(architecture, diane.architecture)

    def test_POST_create_creates_machine_with_subarchitecture(self):
        # The API allows a Machine to be created.
        architecture = make_usable_architecture(self)
        response = self.client.post(
            reverse('machines_handler'),
            {
                'hostname': 'diane',
                'architecture': architecture.split('/')[0],
                'subarchitecture': architecture.split('/')[1],
                'power_type': 'ether_wake',
                'mac_addresses': ['aa:bb:cc:dd:ee:ff', '22:bb:cc:dd:ee:ff'],
            })

        self.assertEqual(http.client.OK, response.status_code)
        parsed_result = json_load_bytes(response.content)
        self.assertIn('application/json', response['Content-Type'])
        domain_name = Domain.objects.get_default_domain().name
        self.assertEqual(
            'diane.%s' % domain_name, parsed_result['hostname'])
        self.assertNotEqual(0, len(parsed_result.get('system_id')))
        [diane] = Machine.objects.filter(hostname='diane')
        self.assertEqual(architecture, diane.architecture)

    def test_POST_create_fails_machine_with_double_subarchitecture(self):
        architecture = make_usable_architecture(self)
        response = self.client.post(
            reverse('machines_handler'),
            {
                'hostname': 'diane',
                'architecture': architecture,
                'subarchitecture': architecture.split('/')[1],
                'mac_addresses': ['aa:bb:cc:dd:ee:ff', '22:bb:cc:dd:ee:ff'],
            })
        self.assertEqual(http.client.BAD_REQUEST, response.status_code)
        self.assertIn('text/plain', response['Content-Type'])
        self.assertEqual(
            b"Subarchitecture cannot be specified twice.",
            response.content)

    def test_POST_create_associates_mac_addresses(self):
        # The API allows a Machine to be created and associated with MAC
        # Addresses.
        architecture = make_usable_architecture(self)
        self.client.post(
            reverse('machines_handler'),
            {
                'hostname': 'diane',
                'architecture': architecture,
                'mac_addresses': ['aa:bb:cc:dd:ee:ff', '22:bb:cc:dd:ee:ff'],
            })
        diane = get_one(Machine.objects.filter(hostname='diane'))
        self.assertItemsEqual(
            ['aa:bb:cc:dd:ee:ff', '22:bb:cc:dd:ee:ff'],
            [interface.mac_address for interface in diane.interface_set.all()])

    def test_POST_create_with_no_hostname_auto_populates_hostname(self):
        architecture = make_usable_architecture(self)
        response = self.client.post(
            reverse('machines_handler'),
            {
                'architecture': architecture,
                'power_type': 'ether_wake',
                'mac_addresses': [factory.make_mac_address()],
            })
        machine = Machine.objects.get(
            system_id=json_load_bytes(response.content)['system_id'])
        self.assertNotEqual("", strip_domain(machine.hostname))

    def test_POST_fails_if_mac_duplicated(self):
        # Mac Addresses should be unique.
        mac = 'aa:bb:cc:dd:ee:ff'
        factory.make_Interface(INTERFACE_TYPE.PHYSICAL, mac_address=MAC(mac))
        architecture = make_usable_architecture(self)
        response = self.client.post(
            reverse('machines_handler'),
            {
                'architecture': architecture,
                'hostname': factory.make_string(),
                'mac_addresses': [mac],
            })
        parsed_result = json_load_bytes(response.content)

        self.assertEqual(http.client.BAD_REQUEST, response.status_code)
        self.assertIn('application/json', response['Content-Type'])
        self.assertIn(
            "MAC address %s already in use on" % mac,
            parsed_result['mac_addresses'][0])

    def test_POST_fails_with_bad_operation(self):
        # If the operation ('op=operation_name') specified in the
        # request data is unknown, a 'Bad request' response is returned.
        response = self.client.post(
            reverse('machines_handler'),
            {
                'op': 'invalid_operation',
                'hostname': 'diane',
                'mac_addresses': ['aa:bb:cc:dd:ee:ff', 'invalid'],
            })

        self.assertEqual(http.client.BAD_REQUEST, response.status_code)
        self.assertEqual(
            b"Unrecognised signature: method=POST op=invalid_operation",
            response.content)

    def test_POST_create_rejects_invalid_data(self):
        # If the data provided to create a machine with an invalid MAC
        # Address, a 'Bad request' response is returned.
        response = self.client.post(
            reverse('machines_handler'),
            {
                'hostname': 'diane',
                'mac_addresses': ['aa:bb:cc:dd:ee:ff', 'invalid'],
            })
        parsed_result = json_load_bytes(response.content)

        self.assertEqual(http.client.BAD_REQUEST, response.status_code)
        self.assertIn('application/json', response['Content-Type'])
        self.assertEqual(
            [
                "One or more MAC addresses is invalid. "
                "('invalid' is not a valid MAC address.)"
            ],
            parsed_result['mac_addresses'])

    def test_POST_invalid_architecture_returns_bad_request(self):
        # If the architecture name provided to create a machine is not a valid
        # architecture name, a 'Bad request' response is returned.
        response = self.client.post(
            reverse('machines_handler'),
            {
                'hostname': 'diane',
                'mac_addresses': ['aa:bb:cc:dd:ee:ff'],
                'architecture': 'invalid-architecture',
            })
        parsed_result = json_load_bytes(response.content)

        self.assertEqual(http.client.BAD_REQUEST, response.status_code)
        self.assertIn('application/json', response['Content-Type'])
        self.assertItemsEqual(
            ['architecture'], parsed_result, response.content)


class MachineHostnameEnlistmentTest(
        MultipleUsersScenarios, MAASServerTestCase):

    def setUp(self):
        super().setUp()
        self.patch(Node, 'get_effective_power_info').return_value = (
            PowerInfo(False, False, False, None, None))

    scenarios = [
        ('anon', dict(userfactory=lambda: AnonymousUser())),
        ('user', dict(userfactory=factory.make_User)),
        ('admin', dict(userfactory=factory.make_admin)),
        ]

    def test_created_machine_gets_default_domain_appended(self):
        hostname_without_domain = factory.make_name('hostname')
        response = self.client.post(
            reverse('machines_handler'),
            {
                'hostname': hostname_without_domain,
                'architecture': make_usable_architecture(self),
                'power_type': 'ether_wake',
                'mac_addresses': [factory.make_mac_address()],
            })
        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        parsed_result = json_load_bytes(response.content)
        expected_hostname = '%s.%s' % (
            hostname_without_domain,
            Domain.objects.get_default_domain().name)
        self.assertEqual(
            expected_hostname, parsed_result.get('hostname'))


class NonAdminEnlistmentAPITest(
        MultipleUsersScenarios, MAASServerTestCase):
    # Enlistment tests for non-admin users.

    scenarios = [
        ('anon', dict(userfactory=lambda: AnonymousUser())),
        ('user', dict(userfactory=factory.make_User)),
        ]

    def setUp(self):
        super().setUp()
        self.patch(Node, 'get_effective_power_info').return_value = (
            PowerInfo(False, False, False, None, None))

    def test_POST_non_admin_creates_machine_in_declared_state(self):
        # Upon non-admin enlistment, a machine goes into the New
        # state.  Deliberate approval is required before we start
        # reinstalling the system, wiping its disks etc.
        response = self.client.post(
            reverse('machines_handler'),
            {
                'hostname': factory.make_string(),
                'architecture': make_usable_architecture(self),
                'mac_addresses': ['aa:bb:cc:dd:ee:ff'],
            })
        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        system_id = json_load_bytes(response.content)['system_id']
        self.assertEqual(
            NODE_STATUS.NEW,
            Machine.objects.get(system_id=system_id).status)


class AnonymousEnlistmentAPITest(MAASServerTestCase):
    # Enlistment tests specific to anonymous users.

    def setUp(self):
        super().setUp()
        self.patch(Node, 'get_effective_power_info').return_value = (
            PowerInfo(False, False, False, None, None))

    def test_POST_accept_not_allowed(self):
        # An anonymous user is not allowed to accept an anonymously
        # enlisted machine.  That would defeat the whole purpose of holding
        # those machines for approval.
        machine_id = factory.make_Node(status=NODE_STATUS.NEW).system_id
        response = self.client.post(
            reverse('machines_handler'),
            {'op': 'accept', 'machines': [machine_id]})
        self.assertEqual(
            (http.client.UNAUTHORIZED,
             b"You must be logged in to accept machines."),
            (response.status_code, response.content))

    def test_POST_returns_limited_fields(self):
        response = self.client.post(
            reverse('machines_handler'),
            {
                'architecture': make_usable_architecture(self),
                'hostname': factory.make_string(),
                'mac_addresses': ['aa:bb:cc:dd:ee:ff', '22:bb:cc:dd:ee:ff'],
            })
        parsed_result = json_load_bytes(response.content)
        self.assertItemsEqual(
            [
                'hostname',
                'owner',
                'system_id',
                'architecture',
                'min_hwe_kernel',
                'hwe_kernel',
                'status',
                'osystem',
                'distro_series',
                'netboot',
                'node_type',
                'power_type',
                'power_state',
                'tag_names',
                'ip_addresses',
                'interface_set',
                'resource_uri',
                'cpu_count',
                'storage',
                'memory',
                'swap_size',
                'zone',
                'disable_ipv4',
                'address_ttl',
                'boot_disk',
                'boot_interface',
                'blockdevice_set',
                'physicalblockdevice_set',
                'virtualblockdevice_set',
                'status_name',
                'status_message',
                'status_action',
            ],
            list(parsed_result))


class SimpleUserLoggedInEnlistmentAPITest(MAASServerTestCase):
    """Enlistment tests from the perspective of regular, non-admin users."""

    def setUp(self):
        super().setUp()
        self.patch(Node, 'get_effective_power_info').return_value = (
            PowerInfo(False, False, False, None, None))

    def test_POST_accept_not_allowed(self):
        # An non-admin user is not allowed to accept an anonymously
        # enlisted machine.  That would defeat the whole purpose of holding
        # those machines for approval.
        self.client_log_in()
        machine_id = factory.make_Node(status=NODE_STATUS.NEW).system_id
        response = self.client.post(
            reverse('machines_handler'),
            {'op': 'accept', 'machines': [machine_id]})
        self.assertEqual(
            (http.client.FORBIDDEN, (
                "You don't have the required permission to accept the "
                "following machine(s): %s." % machine_id).encode(
                settings.DEFAULT_CHARSET)),
            (response.status_code, response.content))

    def test_POST_accept_all_does_not_accept_anything(self):
        # It is not an error for a non-admin user to attempt to accept all
        # anonymously enlisted machines, but only those for which he/she has
        # admin privs will be accepted, which currently equates to none of
        # them.
        self.client_log_in()
        factory.make_Node(status=NODE_STATUS.NEW),
        factory.make_Node(status=NODE_STATUS.NEW),
        response = self.client.post(
            reverse('machines_handler'), {'op': 'accept_all'})
        self.assertEqual(http.client.OK, response.status_code)
        machines_returned = json_load_bytes(response.content)
        self.assertEqual([], machines_returned)

    def test_POST_simple_user_can_set_power_type_and_parameters(self):
        self.client_log_in()
        new_power_address = factory.make_string()
        response = self.client.post(
            reverse('machines_handler'), {
                'architecture': make_usable_architecture(self),
                'power_type': 'ether_wake',
                'power_parameters': json.dumps(
                    {"power_address": new_power_address}),
                'mac_addresses': ['AA:BB:CC:DD:EE:FF'],
                })

        machine = Machine.objects.get(
            system_id=json_load_bytes(response.content)['system_id'])
        self.assertEqual(
            (http.client.OK, {"power_address": new_power_address},
             'ether_wake'),
            (response.status_code, machine.power_parameters,
             machine.power_type))

    def test_POST_returns_limited_fields(self):
        self.client_log_in()
        response = self.client.post(
            reverse('machines_handler'),
            {
                'hostname': factory.make_string(),
                'architecture': make_usable_architecture(self),
                'mac_addresses': ['aa:bb:cc:dd:ee:ff', '22:bb:cc:dd:ee:ff'],
            })
        parsed_result = json_load_bytes(response.content)
        self.assertItemsEqual(
            [
                'hostname',
                'owner',
                'system_id',
                'macaddress_set',
                'architecture',
                'min_hwe_kernel',
                'hwe_kernel',
                'status',
                'osystem',
                'distro_series',
                'netboot',
                'node_type',
                'node_type_name',
                'power_type',
                'power_state',
                'resource_uri',
                'tag_names',
                'ip_addresses',
                'interface_set',
                'cpu_count',
                'storage',
                'memory',
                'swap_size',
                'zone',
                'disable_ipv4',
                'address_ttl',
                'boot_disk',
                'boot_interface',
                'blockdevice_set',
                'physicalblockdevice_set',
                'virtualblockdevice_set',
                'status_action',
                'status_message',
                'status_name',
            ],
            list(parsed_result))


class AdminLoggedInEnlistmentAPITest(MAASServerTestCase):
    """Enlistment tests from the perspective of admin users."""

    def setUp(self):
        super().setUp()
        self.patch(Node, 'get_effective_power_info').return_value = (
            PowerInfo(False, False, False, None, None))

    def test_POST_sets_power_type_if_admin(self):
        self.client_log_in(as_admin=True)
        response = self.client.post(
            reverse('machines_handler'), {
                'architecture': make_usable_architecture(self),
                'power_type': 'ether_wake',
                'mac_addresses': ['00:11:22:33:44:55'],
                })
        self.assertEqual(http.client.OK, response.status_code)
        machine = Machine.objects.get(
            system_id=json_load_bytes(response.content)['system_id'])
        self.assertEqual('ether_wake', machine.power_type)
        self.assertEqual({}, machine.power_parameters)

    def test_POST_sets_power_parameters_field(self):
        # The api allows the setting of a Machine's power_parameters field.
        # Create a power_parameter valid for the selected power_type.
        self.client_log_in(as_admin=True)
        new_mac_address = factory.make_mac_address()
        response = self.client.post(
            reverse('machines_handler'), {
                'architecture': make_usable_architecture(self),
                'power_type': 'ether_wake',
                'power_parameters_mac_address': new_mac_address,
                'mac_addresses': ['AA:BB:CC:DD:EE:FF'],
                })

        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        machine = Machine.objects.get(
            system_id=json_load_bytes(response.content)['system_id'])
        self.assertEqual(
            {'mac_address': new_mac_address},
            reload_object(machine).power_parameters)

    def test_POST_updates_power_parameters_rejects_unknown_param(self):
        self.client_log_in(as_admin=True)
        hostname = factory.make_string()
        response = self.client.post(
            reverse('machines_handler'), {
                'hostname': hostname,
                'architecture': make_usable_architecture(self),
                'power_type': 'ether_wake',
                'power_parameters_unknown_param': factory.make_string(),
                'mac_addresses': [factory.make_mac_address()],
                })

        self.assertEqual(
            (
                http.client.BAD_REQUEST,
                {'power_parameters': ["Unknown parameter(s): unknown_param."]}
            ),
            (response.status_code, json_load_bytes(response.content)))
        self.assertFalse(Machine.objects.filter(hostname=hostname).exists())

    def test_POST_new_sets_power_parameters_skip_check(self):
        # The api allows to skip the validation step and set arbitrary
        # power parameters.
        self.client_log_in(as_admin=True)
        param = factory.make_string()
        response = self.client.post(
            reverse('machines_handler'), {
                'architecture': make_usable_architecture(self),
                'power_type': 'ether_wake',
                'power_parameters_param': param,
                'power_parameters_skip_check': 'true',
                'mac_addresses': ['AA:BB:CC:DD:EE:FF'],
                })

        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        machine = Machine.objects.get(
            system_id=json_load_bytes(response.content)['system_id'])
        self.assertEqual(
            {'param': param},
            reload_object(machine).power_parameters)

    def test_POST_admin_creates_machine_in_commissioning_state(self):
        # When an admin user enlists a machine, it goes into the
        # Commissioning state.
        self.client_log_in(as_admin=True)
        response = self.client.post(
            reverse('machines_handler'),
            {
                'hostname': factory.make_string(),
                'architecture': make_usable_architecture(self),
                'power_type': 'ether_wake',
                'mac_addresses': ['aa:bb:cc:dd:ee:ff'],
            })
        self.assertEqual(http.client.OK, response.status_code)
        system_id = json_load_bytes(response.content)['system_id']
        self.assertEqual(
            NODE_STATUS.COMMISSIONING,
            Machine.objects.get(system_id=system_id).status)

    def test_POST_returns_limited_fields(self):
        self.client_log_in(as_admin=True)
        response = self.client.post(
            reverse('machines_handler'),
            {
                'hostname': factory.make_string(),
                'architecture': make_usable_architecture(self),
                'power_type': 'ether_wake',
                'mac_addresses': ['aa:bb:cc:dd:ee:ff', '22:bb:cc:dd:ee:ff'],
            })
        parsed_result = json_load_bytes(response.content)
        self.assertItemsEqual(
            [
                'hostname',
                'owner',
                'system_id',
                'macaddress_set',
                'architecture',
                'min_hwe_kernel',
                'hwe_kernel',
                'status',
                'osystem',
                'distro_series',
                'netboot',
                'node_type',
                'node_type_name',
                'power_type',
                'power_state',
                'resource_uri',
                'tag_names',
                'ip_addresses',
                'interface_set',
                'cpu_count',
                'storage',
                'memory',
                'swap_size',
                'zone',
                'disable_ipv4',
                'address_ttl',
                'boot_disk',
                'boot_interface',
                'blockdevice_set',
                'physicalblockdevice_set',
                'virtualblockdevice_set',
                'status_name',
                'status_message',
                'status_action'
            ],
            list(parsed_result))

    def test_POST_accept_all(self):
        # An admin user can accept all anonymously enlisted machines.
        self.client_log_in(as_admin=True)
        machines = [
            factory.make_Node(status=NODE_STATUS.NEW),
            factory.make_Node(status=NODE_STATUS.NEW),
            ]
        response = self.client.post(
            reverse('machines_handler'), {'op': 'accept_all'})
        self.assertEqual(http.client.OK, response.status_code)
        machines_returned = json_load_bytes(response.content)
        self.assertSetEqual(
            {machine.system_id for machine in machines},
            {machine["system_id"] for machine in machines_returned})
