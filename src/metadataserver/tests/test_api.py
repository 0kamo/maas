# Copyright 2012-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for the metadata API."""

__all__ = []

from collections import namedtuple
from datetime import datetime
import http.client
from io import BytesIO
import json
import os.path
import random
import tarfile
from unittest.mock import (
    ANY,
    Mock,
)

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.urlresolvers import reverse
from maasserver import preseed as preseed_module
from maasserver.clusterrpc.testing.boot_images import make_rpc_boot_image
from maasserver.enum import (
    NODE_STATUS,
    NODE_TYPE,
    NODE_TYPE_CHOICES,
    POWER_STATE,
)
from maasserver.exceptions import (
    MAASAPINotFound,
    Unauthorized,
)
from maasserver.models import (
    Event,
    SSHKey,
    Tag,
)
from maasserver.models.node import Node
from maasserver.models.signals.testing import SignalsDisabled
from maasserver.rpc.testing.mixins import PreseedRPCMixin
from maasserver.testing.config import RegionConfigurationFixture
from maasserver.testing.factory import factory
from maasserver.testing.testcase import (
    MAASServerTestCase,
    MAASTransactionServerTestCase,
)
from maasserver.testing.testclient import MAASSensibleOAuthClient
from maasserver.utils.orm import reload_object
from maastesting.matchers import (
    MockCalledOnceWith,
    MockNotCalled,
)
from maastesting.utils import sample_binary_data
from metadataserver import api
from metadataserver.api import (
    add_event_to_node_event_log,
    check_version,
    get_node_for_mac,
    get_node_for_request,
    get_queried_node,
    make_list_response,
    make_text_response,
    MetaDataHandler,
    poweroff as api_poweroff,
    UnknownMetadataVersion,
)
from metadataserver.enum import RESULT_TYPE
from metadataserver.models import (
    NodeKey,
    NodeResult,
    NodeUserData,
)
from metadataserver.models.commissioningscript import ARCHIVE_PREFIX
from metadataserver.nodeinituser import get_node_init_user
from netaddr import IPNetwork
from provisioningserver.events import (
    EVENT_DETAILS,
    EVENT_TYPES,
)
from testtools.matchers import (
    Contains,
    ContainsAll,
    Equals,
    MatchesAll,
    Not,
)


class TestHelpers(MAASServerTestCase):
    """Tests for the API helper functions."""

    def fake_request(self, **kwargs):
        """Produce a cheap fake request, fresh from the sweat shop.

        Pass as arguments any header items you want to include.
        """
        return namedtuple('FakeRequest', ['META'])(kwargs)

    def test_make_text_response_presents_text_as_text_plain(self):
        input_text = "Hello."
        response = make_text_response(input_text)
        self.assertEqual('text/plain', response['Content-Type'])
        self.assertEqual(
            input_text, response.content.decode(settings.DEFAULT_CHARSET))

    def test_make_list_response_presents_list_as_newline_separated_text(self):
        response = make_list_response(['aaa', 'bbb'])
        self.assertEqual('text/plain', response['Content-Type'])
        self.assertEqual(
            "aaa\nbbb", response.content.decode(settings.DEFAULT_CHARSET))

    def test_check_version_accepts_latest(self):
        check_version('latest')
        # The test is that we get here without exception.
        pass

    def test_check_version_reports_unknown_version(self):
        self.assertRaises(UnknownMetadataVersion, check_version, '2.0')

    def test_get_node_for_request_finds_node(self):
        node = factory.make_Node()
        token = NodeKey.objects.get_token_for_node(node)
        request = self.fake_request(
            HTTP_AUTHORIZATION=factory.make_oauth_header(
                oauth_token=token.key))
        self.assertEqual(node, get_node_for_request(request))

    def test_get_node_for_request_reports_missing_auth_header(self):
        self.assertRaises(
            Unauthorized,
            get_node_for_request, self.fake_request())

    def test_get_node_for_mac_refuses_if_anonymous_access_disabled(self):
        self.patch(settings, 'ALLOW_UNSAFE_METADATA_ACCESS', False)
        self.assertRaises(
            PermissionDenied, get_node_for_mac, factory.make_mac_address())

    def test_get_node_for_mac_raises_404_for_unknown_mac(self):
        self.assertRaises(
            MAASAPINotFound, get_node_for_mac, factory.make_mac_address())

    def test_get_node_for_mac_finds_node_by_mac(self):
        node = factory.make_Node_with_Interface_on_Subnet()
        iface = node.get_boot_interface()
        self.assertEqual(iface.node, get_node_for_mac(iface.mac_address))

    def test_get_queried_node_looks_up_by_mac_if_given(self):
        node = factory.make_Node_with_Interface_on_Subnet()
        iface = node.get_boot_interface()
        self.assertEqual(
            iface.node,
            get_queried_node(object(), for_mac=iface.mac_address))

    def test_get_queried_node_looks_up_oauth_key_by_default(self):
        node = factory.make_Node()
        token = NodeKey.objects.get_token_for_node(node)
        request = self.fake_request(
            HTTP_AUTHORIZATION=factory.make_oauth_header(
                oauth_token=token.key))
        self.assertEqual(node, get_queried_node(request))

    def test_add_event_to_node_event_log(self):
        expected_type = {
            # These statuses have specific event types.
            NODE_STATUS.COMMISSIONING: EVENT_TYPES.NODE_COMMISSIONING_EVENT,
            NODE_STATUS.DEPLOYING: EVENT_TYPES.NODE_INSTALL_EVENT,

            # All other statuses generate NODE_STATUS_EVENT events.
            NODE_STATUS.NEW: EVENT_TYPES.NODE_STATUS_EVENT,
            NODE_STATUS.FAILED_COMMISSIONING: EVENT_TYPES.NODE_STATUS_EVENT,
            NODE_STATUS.MISSING: EVENT_TYPES.NODE_STATUS_EVENT,
            NODE_STATUS.READY: EVENT_TYPES.NODE_STATUS_EVENT,
            NODE_STATUS.RESERVED: EVENT_TYPES.NODE_STATUS_EVENT,
            NODE_STATUS.ALLOCATED: EVENT_TYPES.NODE_STATUS_EVENT,
            NODE_STATUS.DEPLOYED: EVENT_TYPES.NODE_STATUS_EVENT,
            NODE_STATUS.RETIRED: EVENT_TYPES.NODE_STATUS_EVENT,
            NODE_STATUS.BROKEN: EVENT_TYPES.NODE_STATUS_EVENT,
            NODE_STATUS.FAILED_DEPLOYMENT: EVENT_TYPES.NODE_STATUS_EVENT,
            NODE_STATUS.RELEASING: EVENT_TYPES.NODE_STATUS_EVENT,
            NODE_STATUS.FAILED_RELEASING: EVENT_TYPES.NODE_STATUS_EVENT,
            NODE_STATUS.DISK_ERASING: EVENT_TYPES.NODE_STATUS_EVENT,
            NODE_STATUS.FAILED_DISK_ERASING: EVENT_TYPES.NODE_STATUS_EVENT,
        }

        for status in expected_type:
            node = factory.make_Node(status=status)
            origin = factory.make_name('origin')
            action = factory.make_name('action')
            description = factory.make_name('description')
            add_event_to_node_event_log(node, origin, action, description)
            event = Event.objects.get(node=node)

            self.assertEqual(node, event.node)
            self.assertEqual(action, event.action)
            self.assertIn(origin, event.description)
            self.assertIn(description, event.description)
            self.assertEqual(expected_type[node.status], event.type.name)

    def test_add_event_to_node_event_log_logs_rack_refresh(self):
        rack = factory.make_RackController()
        origin = factory.make_name('origin')
        action = factory.make_name('action')
        description = factory.make_name('description')
        add_event_to_node_event_log(rack, origin, action, description)
        event = Event.objects.get(node=rack)

        self.assertEqual(rack, event.node)
        self.assertEqual(action, event.action)
        self.assertIn(origin, event.description)
        self.assertIn(description, event.description)
        self.assertEqual(
            EVENT_TYPES.REQUEST_CONTROLLER_REFRESH, event.type.name)


def make_node_client(node=None):
    """Create a test client logged in as if it were `node`."""
    if node is None:
        node = factory.make_Node()
    token = NodeKey.objects.get_token_for_node(node)
    return MAASSensibleOAuthClient(get_node_init_user(), token)


def call_signal(client=None, version='latest', files={}, headers={}, **kwargs):
    """Call the API's signal method.

    :param client: Optional client to POST with.  If omitted, will create
        one for a commissioning node.
    :param version: API version to post on.  Defaults to "latest".
    :param files: Optional dict of files to attach.  Maps file name to
        file contents.
    :param **kwargs: Any other keyword parameters are passed on directly
        to the "signal" call.
    """
    if client is None:
        client = make_node_client(factory.make_Node(
            status=NODE_STATUS.COMMISSIONING))
    params = {
        'op': 'signal',
        'status': 'OK',
    }
    params.update(kwargs)
    params.update({
        name: factory.make_file_upload(name, content)
        for name, content in files.items()
    })
    url = reverse('metadata-version', args=[version])
    return client.post(url, params, **headers)


class TestMetadataCommon(MAASServerTestCase):
    """Tests for the common metadata/curtin-metadata API views."""

    # The curtin-metadata and the metadata views are similar in every
    # aspect except the user-data end-point.  The same tests are used to
    # test both end-points.
    scenarios = [
        ('metadata', {'metadata_prefix': 'metadata'}),
        ('curtin-metadata', {'metadata_prefix': 'curtin-metadata'}),
    ]

    def get_metadata_name(self, name_suffix=''):
        """Return the Django name of the metadata view.

        :param name_suffix: Suffix of the view name.  The default value is
            the empty string (get_metadata_name() will return the root of
            the metadata API in this case).

        Depending on the value of self.metadata_prefix, this will return
        the name of the metadata view or of the curtin-metadata view.
        """
        return self.metadata_prefix + name_suffix

    def test_no_anonymous_access(self):
        url = reverse(self.get_metadata_name())
        self.assertEqual(
            http.client.UNAUTHORIZED, self.client.get(url).status_code)

    def test_metadata_index_shows_latest(self):
        client = make_node_client()
        url = reverse(self.get_metadata_name())
        content = client.get(url).content.decode(settings.DEFAULT_CHARSET)
        self.assertIn('latest', content)

    def test_metadata_index_shows_only_known_versions(self):
        client = make_node_client()
        url = reverse(self.get_metadata_name())
        content = client.get(url).content.decode(settings.DEFAULT_CHARSET)
        for item in content.splitlines():
            check_version(item)
        # The test is that we get here without exception.
        pass

    def test_version_index_shows_unconditional_entries(self):
        client = make_node_client()
        view_name = self.get_metadata_name('-version')
        url = reverse(view_name, args=['latest'])
        content = client.get(url).content.decode(settings.DEFAULT_CHARSET)
        self.assertThat(content.splitlines(), ContainsAll([
            'meta-data',
            'maas-commissioning-scripts',
            ]))

    def test_version_index_does_not_show_user_data_if_not_available(self):
        client = make_node_client()
        view_name = self.get_metadata_name('-version')
        url = reverse(view_name, args=['latest'])
        content = client.get(url).content.decode(settings.DEFAULT_CHARSET)
        self.assertNotIn('user-data', content.splitlines())

    def test_version_index_shows_user_data_if_available(self):
        node = factory.make_Node()
        NodeUserData.objects.set_user_data(node, b"User data for node")
        client = make_node_client(node)
        view_name = self.get_metadata_name('-version')
        url = reverse(view_name, args=['latest'])
        content = client.get(url).content.decode(settings.DEFAULT_CHARSET)
        self.assertIn('user-data', content.splitlines())

    def test_meta_data_view_lists_fields(self):
        # Some fields only are returned if there is data related to them.
        user, _ = factory.make_user_with_keys(n_keys=2, username='my-user')
        node = factory.make_Node(owner=user)
        client = make_node_client(node=node)
        view_name = self.get_metadata_name('-meta-data')
        url = reverse(view_name, args=['latest', ''])
        response = client.get(url)
        self.assertIn('text/plain', response['Content-Type'])
        self.assertItemsEqual(
            MetaDataHandler.fields, [
                field.decode(settings.DEFAULT_CHARSET)
                for field in response.content.split()
            ])

    def test_meta_data_view_is_sorted(self):
        client = make_node_client()
        view_name = self.get_metadata_name('-meta-data')
        url = reverse(view_name, args=['latest', ''])
        response = client.get(url)
        attributes = response.content.split()
        self.assertEqual(sorted(attributes), attributes)

    def test_meta_data_unknown_item_is_not_found(self):
        client = make_node_client()
        view_name = self.get_metadata_name('-meta-data')
        url = reverse(view_name, args=['latest', 'UNKNOWN-ITEM'])
        response = client.get(url)
        self.assertEqual(http.client.NOT_FOUND, response.status_code)

    def test_get_attribute_producer_supports_all_fields(self):
        handler = MetaDataHandler()
        producers = list(map(handler.get_attribute_producer, handler.fields))
        self.assertNotIn(None, producers)

    def test_meta_data_local_hostname_returns_fqdn(self):
        hostname = factory.make_string()
        domain = factory.make_Domain()
        node = factory.make_Node(
            hostname='%s.%s' % (hostname, domain.name))
        client = make_node_client(node)
        view_name = self.get_metadata_name('-meta-data')
        url = reverse(view_name, args=['latest', 'local-hostname'])
        response = client.get(url)
        self.assertEqual(
            (http.client.OK, node.fqdn),
            (response.status_code,
             response.content.decode(settings.DEFAULT_CHARSET)))
        self.assertIn('text/plain', response['Content-Type'])

    def test_meta_data_instance_id_returns_system_id(self):
        node = factory.make_Node()
        client = make_node_client(node)
        view_name = self.get_metadata_name('-meta-data')
        url = reverse(view_name, args=['latest', 'instance-id'])
        response = client.get(url)
        self.assertEqual(
            (http.client.OK, node.system_id),
            (response.status_code,
             response.content.decode(settings.DEFAULT_CHARSET)))
        self.assertIn('text/plain', response['Content-Type'])

    def test_public_keys_not_listed_for_node_without_public_keys(self):
        view_name = self.get_metadata_name('-meta-data')
        url = reverse(view_name, args=['latest', ''])
        client = make_node_client()
        response = client.get(url)
        self.assertNotIn(
            'public-keys', response.content.decode(
                settings.DEFAULT_CHARSET).split('\n'))

    def test_public_keys_not_listed_for_comm_node_with_ssh_disabled(self):
        user, _ = factory.make_user_with_keys(n_keys=2, username='my-user')
        node = factory.make_Node(
            owner=user, status=NODE_STATUS.COMMISSIONING, enable_ssh=False)
        view_name = self.get_metadata_name('-meta-data')
        url = reverse(view_name, args=['latest', ''])
        client = make_node_client(node=node)
        response = client.get(url)
        self.assertNotIn(
            'public-keys', response.content.decode(
                settings.DEFAULT_CHARSET).split('\n'))

    def test_public_keys_listed_for_comm_node_with_ssh_enabled(self):
        user, _ = factory.make_user_with_keys(n_keys=2, username='my-user')
        node = factory.make_Node(
            owner=user, status=NODE_STATUS.COMMISSIONING, enable_ssh=True)
        view_name = self.get_metadata_name('-meta-data')
        url = reverse(view_name, args=['latest', ''])
        client = make_node_client(node=node)
        response = client.get(url)
        self.assertIn(
            'public-keys', response.content.decode(
                settings.DEFAULT_CHARSET).split('\n'))

    def test_public_keys_listed_for_node_with_public_keys(self):
        user, _ = factory.make_user_with_keys(n_keys=2, username='my-user')
        node = factory.make_Node(owner=user)
        view_name = self.get_metadata_name('-meta-data')
        url = reverse(view_name, args=['latest', ''])
        client = make_node_client(node=node)
        response = client.get(url)
        self.assertIn(
            'public-keys', response.content.decode(
                settings.DEFAULT_CHARSET).split('\n'))

    def test_public_keys_for_node_without_public_keys_returns_empty(self):
        view_name = self.get_metadata_name('-meta-data')
        url = reverse(view_name, args=['latest', 'public-keys'])
        client = make_node_client()
        response = client.get(url)
        self.assertEqual(
            (http.client.OK, b''),
            (response.status_code, response.content))

    def test_public_keys_for_node_returns_list_of_keys(self):
        user, _ = factory.make_user_with_keys(n_keys=2, username='my-user')
        node = factory.make_Node(owner=user)
        view_name = self.get_metadata_name('-meta-data')
        url = reverse(view_name, args=['latest', 'public-keys'])
        client = make_node_client(node=node)
        response = client.get(url)
        self.assertEqual(http.client.OK, response.status_code)
        keys = SSHKey.objects.filter(user=user).values_list('key', flat=True)
        expected_response = '\n'.join(keys)
        self.assertThat(
            response.content.decode(settings.DEFAULT_CHARSET),
            Equals(expected_response))
        self.assertIn('text/plain', response['Content-Type'])

    def test_public_keys_url_with_additional_slashes(self):
        # The metadata service also accepts urls with any number of additional
        # slashes after 'metadata': e.g. http://host/metadata///rest-of-url.
        user, _ = factory.make_user_with_keys(n_keys=2, username='my-user')
        node = factory.make_Node(owner=user)
        view_name = self.get_metadata_name('-meta-data')
        url = reverse(view_name, args=['latest', 'public-keys'])
        # Insert additional slashes.
        url = url.replace('metadata', 'metadata/////')
        client = make_node_client(node=node)
        response = client.get(url)
        keys = SSHKey.objects.filter(user=user).values_list('key', flat=True)
        self.assertThat(
            response.content.decode(settings.DEFAULT_CHARSET),
            Equals('\n'.join(keys)))


class TestMetadataUserData(MAASServerTestCase):
    """Tests for the metadata user-data API endpoint."""

    def test_user_data_view_returns_binary_data(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        NodeUserData.objects.set_user_data(node, sample_binary_data)
        client = make_node_client(node)
        response = client.get(reverse('metadata-user-data', args=['latest']))
        self.assertEqual('application/octet-stream', response['Content-Type'])
        self.assertIsInstance(response.content, bytes)
        self.assertEqual(
            (http.client.OK, sample_binary_data),
            (response.status_code, response.content))

    def test_poweroff_user_data_returned_if_unexpected_status(self):
        node = factory.make_Node(status=NODE_STATUS.READY)
        NodeUserData.objects.set_user_data(node, sample_binary_data)
        client = make_node_client(node)
        user_data = factory.make_name('user data').encode("ascii")
        self.patch(api_poweroff, 'generate_user_data').return_value = user_data
        response = client.get(reverse('metadata-user-data', args=['latest']))
        self.assertEqual('application/octet-stream', response['Content-Type'])
        self.assertIsInstance(response.content, bytes)
        self.assertEqual(
            (http.client.OK, user_data),
            (response.status_code, response.content))

    def test_user_data_for_node_without_user_data_returns_not_found(self):
        client = make_node_client(
            factory.make_Node(status=NODE_STATUS.COMMISSIONING))
        response = client.get(reverse('metadata-user-data', args=['latest']))
        self.assertEqual(http.client.NOT_FOUND, response.status_code)


class TestMetadataUserDataStateChanges(MAASServerTestCase):
    """Tests for the metadata user-data API endpoint."""

    def setUp(self):
        super(TestMetadataUserDataStateChanges, self).setUp()
        self.useFixture(SignalsDisabled("power"))

    def test_request_does_not_cause_status_change_if_not_deploying(self):
        status = factory.pick_enum(
            NODE_STATUS, but_not=[NODE_STATUS.DEPLOYING])
        node = factory.make_Node(status=status)
        NodeUserData.objects.set_user_data(node, sample_binary_data)
        client = make_node_client(node)
        response = client.get(reverse('metadata-user-data', args=['latest']))
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(status, reload_object(node).status)

    def test_request_causes_status_change_if_deploying(self):
        node = factory.make_Node(status=NODE_STATUS.DEPLOYING)
        NodeUserData.objects.set_user_data(node, sample_binary_data)
        client = make_node_client(node)
        response = client.get(reverse('metadata-user-data', args=['latest']))
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(NODE_STATUS.DEPLOYED, reload_object(node).status)


class TestCurtinMetadataUserData(
        PreseedRPCMixin, MAASTransactionServerTestCase):
    """Tests for the curtin-metadata user-data API endpoint."""

    def test_curtin_user_data_view_returns_curtin_data(self):
        node = factory.make_Node(interface=True)
        nic = node.get_boot_interface()
        nic.vlan.dhcp_on = True
        nic.vlan.primary_rack = self.rpc_rack_controller
        nic.vlan.save()
        arch, subarch = node.architecture.split('/')
        boot_image = make_rpc_boot_image(purpose='xinstall')
        self.patch(
            preseed_module,
            'get_boot_images_for').return_value = [boot_image]
        client = make_node_client(node)
        response = client.get(
            reverse('curtin-metadata-user-data', args=['latest']))

        self.assertEqual(http.client.OK.value, response.status_code)
        self.assertThat(
            response.content.decode(settings.DEFAULT_CHARSET),
            Contains("PREFIX='curtin'"))


class TestInstallingAPI(MAASServerTestCase):

    def setUp(self):
        super(TestInstallingAPI, self).setUp()
        self.useFixture(SignalsDisabled("power"))

    def test_other_user_than_node_cannot_signal_installation_result(self):
        node = factory.make_Node(status=NODE_STATUS.DEPLOYING)
        client = MAASSensibleOAuthClient(factory.make_User())
        response = call_signal(client)
        self.assertEqual(http.client.FORBIDDEN, response.status_code)
        self.assertEqual(
            NODE_STATUS.DEPLOYING, reload_object(node).status)

    def test_signaling_installation_result_does_not_affect_other_node(self):
        node = factory.make_Node(status=NODE_STATUS.DEPLOYING)
        client = make_node_client(
            node=factory.make_Node(status=NODE_STATUS.DEPLOYING))
        response = call_signal(client, status='OK')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.DEPLOYING, reload_object(node).status)

    def test_signaling_installation_success_leaves_node_deploying(self):
        node = factory.make_Node(interface=True, status=NODE_STATUS.DEPLOYING)
        client = make_node_client(node=node)
        response = call_signal(client, status='OK')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(NODE_STATUS.DEPLOYING, reload_object(node).status)

    def test_signaling_installation_success_does_not_populate_tags(self):
        populate_tags_for_single_node = self.patch(
            api, "populate_tags_for_single_node")
        node = factory.make_Node(interface=True, status=NODE_STATUS.DEPLOYING)
        client = make_node_client(node=node)
        response = call_signal(client, status='OK')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(NODE_STATUS.DEPLOYING, reload_object(node).status)
        self.assertThat(populate_tags_for_single_node, MockNotCalled())

    def test_signaling_installation_success_is_idempotent(self):
        node = factory.make_Node(status=NODE_STATUS.DEPLOYING)
        client = make_node_client(node=node)
        call_signal(client, status='OK')
        response = call_signal(client, status='OK')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(NODE_STATUS.DEPLOYING, reload_object(node).status)

    def test_signaling_installation_success_does_not_clear_owner(self):
        node = factory.make_Node(
            status=NODE_STATUS.DEPLOYING, owner=factory.make_User())
        client = make_node_client(node=node)
        response = call_signal(client, status='OK')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(node.owner, reload_object(node).owner)

    def test_signaling_installation_failure_makes_node_failed(self):
        node = factory.make_Node(
            status=NODE_STATUS.DEPLOYING, owner=factory.make_User())
        client = make_node_client(node=node)
        response = call_signal(client, status='FAILED')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.FAILED_DEPLOYMENT, reload_object(node).status)

    def test_signaling_installation_failure_is_idempotent(self):
        node = factory.make_Node(
            status=NODE_STATUS.DEPLOYING, owner=factory.make_User())
        client = make_node_client(node=node)
        call_signal(client, status='FAILED')
        response = call_signal(client, status='FAILED')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.FAILED_DEPLOYMENT, reload_object(node).status)


class TestCommissioningAPI(MAASServerTestCase):

    def setUp(self):
        super(TestCommissioningAPI, self).setUp()
        self.useFixture(SignalsDisabled("power"))

    def test_commissioning_scripts(self):
        script = factory.make_CommissioningScript()
        response = make_node_client().get(
            reverse('commissioning-scripts', args=['latest']))
        self.assertEqual(
            http.client.OK, response.status_code,
            "Unexpected response %d: %s"
            % (response.status_code, response.content))
        self.assertIn(
            response['Content-Type'],
            {
                'application/tar',
                'application/x-gtar',
                'application/x-tar',
                'application/x-tgz',
            })
        archive = tarfile.open(fileobj=BytesIO(response.content))
        self.assertIn(
            os.path.join(ARCHIVE_PREFIX, script.name),
            archive.getnames())

    def test_other_user_than_node_cannot_signal_commissioning_result(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = MAASSensibleOAuthClient(factory.make_User())
        response = call_signal(client)
        self.assertEqual(http.client.FORBIDDEN, response.status_code)
        self.assertEqual(
            NODE_STATUS.COMMISSIONING, reload_object(node).status)

    def test_signaling_commissioning_result_does_not_affect_other_node(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(
            node=factory.make_Node(status=NODE_STATUS.COMMISSIONING))
        response = call_signal(client, status='OK')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.COMMISSIONING, reload_object(node).status)

    def test_signaling_commissioning_OK_repopulates_tags(self):
        populate_tags_for_single_node = self.patch(
            api, "populate_tags_for_single_node")
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node)
        response = call_signal(client, status='OK', script_result='0')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(NODE_STATUS.READY, reload_object(node).status)
        self.assertThat(
            populate_tags_for_single_node,
            MockCalledOnceWith(ANY, node))

    def test_signaling_requires_status_code(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        url = reverse('metadata-version', args=['latest'])
        response = client.post(url, {'op': 'signal'})
        self.assertEqual(http.client.BAD_REQUEST, response.status_code)

    def test_signaling_rejects_unknown_status_code(self):
        response = call_signal(status=factory.make_string())
        self.assertEqual(http.client.BAD_REQUEST, response.status_code)

    def test_signaling_refuses_if_machine_in_unexpected_state(self):
        machine = factory.make_Node(status=NODE_STATUS.NEW)
        client = make_node_client(node=machine)
        response = call_signal(client)
        self.expectThat(
            response.status_code,
            Equals(http.client.CONFLICT))
        self.expectThat(
            response.content.decode(settings.DEFAULT_CHARSET),
            Equals(
                "Machine wasn't commissioning/installing/entering-rescue-mode"
                " (status is New)"))

    def test_signaling_accepts_non_machine_results(self):
        node = factory.make_Node(
            node_type=factory.pick_choice(
                NODE_TYPE_CHOICES, but_not=[NODE_TYPE.MACHINE]))
        client = make_node_client(node=node)
        script_result = random.randint(0, 10)
        filename = factory.make_string()
        response = call_signal(
            client, script_result=script_result,
            files={filename: factory.make_string().encode('ascii')})
        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        result = NodeResult.objects.get(node=node)
        self.assertEqual(RESULT_TYPE.COMMISSIONING, result.result_type)
        self.assertEqual(script_result, result.script_result)

    def test_signaling_accepts_WORKING_status(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        response = call_signal(client, status='WORKING')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.COMMISSIONING, reload_object(node).status)

    def test_signaling_stores_script_result(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        script_result = random.randint(0, 10)
        filename = factory.make_string()
        response = call_signal(
            client, script_result=script_result,
            files={filename: factory.make_string().encode('ascii')})
        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        result = NodeResult.objects.get(node=node)
        self.assertEqual(script_result, result.script_result)

    def test_signaling_stores_empty_script_result(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        response = call_signal(
            client, script_result=random.randint(0, 10),
            files={factory.make_string(): ''.encode('ascii')})
        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        result = NodeResult.objects.get(node=node)
        self.assertEqual(b'', result.data)

    def test_signaling_WORKING_keeps_owner(self):
        user = factory.make_User()
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        node.owner = user
        node.save()
        client = make_node_client(node=node)
        response = call_signal(client, status='WORKING')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(user, reload_object(node).owner)

    def test_signaling_commissioning_success_makes_node_Ready(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        response = call_signal(client, status='OK')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(NODE_STATUS.READY, reload_object(node).status)

    def test_signalling_commissioning_success_clears_status_expires(self):
        node = factory.make_Node(
            status=NODE_STATUS.COMMISSIONING, status_expires=datetime.now())
        client = make_node_client(node=node)
        response = call_signal(client, status='OK')
        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        self.assertIsNone(reload_object(node).status_expires)

    def test_signaling_commissioning_success_is_idempotent(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        call_signal(client, status='OK')
        response = call_signal(client, status='OK')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(NODE_STATUS.READY, reload_object(node).status)

    def test_signaling_commissioning_success_clears_owner_on_machine(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        node.owner = factory.make_User()
        node.save()
        client = make_node_client(node=node)
        response = call_signal(client, status='OK')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertIsNone(reload_object(node).owner)

    def test_signaling_commissioning_failure_makes_node_Failed_Tests(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        response = call_signal(client, status='FAILED')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.FAILED_COMMISSIONING, reload_object(node).status)

    def test_signaling_commissioning_failure_does_not_populate_tags(self):
        populate_tags_for_single_node = self.patch(
            api, "populate_tags_for_single_node")
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        response = call_signal(client, status='FAILED')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertThat(populate_tags_for_single_node, MockNotCalled())

    def test_signalling_commissioning_clears_status_expires(self):
        node = factory.make_Node(
            status=NODE_STATUS.COMMISSIONING, status_expires=datetime.now())
        client = make_node_client(node=node)
        response = call_signal(client, status='FAILED')
        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        self.assertIsNone(reload_object(node).status_expires)

    def test_signaling_commissioning_failure_is_idempotent(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        call_signal(client, status='FAILED')
        response = call_signal(client, status='FAILED')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.FAILED_COMMISSIONING, reload_object(node).status)

    def test_signaling_commissioning_failure_sets_node_error(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        error_text = factory.make_string()
        response = call_signal(client, status='FAILED', error=error_text)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(error_text, reload_object(node).error)

    def test_signaling_commissioning_failure_clears_owner(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        node.owner = factory.make_User()
        node.save()
        client = make_node_client(node=node)
        response = call_signal(client, status='FAILED')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertIsNone(reload_object(node).owner)

    def test_signaling_no_error_clears_existing_error(self):
        node = factory.make_Node(
            status=NODE_STATUS.COMMISSIONING, error=factory.make_string())
        client = make_node_client(node=node)
        response = call_signal(client)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual('', reload_object(node).error)

    def test_signalling_stores_files_for_any_status(self):
        self.useFixture(SignalsDisabled("power"))
        statuses = ['WORKING', 'OK', 'FAILED']
        filename = factory.make_string()
        nodes = {
            status: factory.make_Node(status=NODE_STATUS.COMMISSIONING)
            for status in statuses}
        for status, node in nodes.items():
            client = make_node_client(node=node)
            script_result = random.randint(0, 10)
            call_signal(
                client, status=status,
                script_result=script_result,
                files={filename: factory.make_bytes()})
        self.assertEqual(
            {status: filename for status in statuses},
            {
                status: NodeResult.objects.get(node=node).name
                for status, node in nodes.items()})

    def test_signal_stores_file_contents(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        text = factory.make_string().encode('ascii')
        script_result = random.randint(0, 10)
        response = call_signal(
            client, script_result=script_result, files={'file.txt': text})
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            text, NodeResult.objects.get_data(node, 'file.txt'))

    def test_signal_stores_binary(self):
        unicode_text = '<\u2621>'
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        script_result = random.randint(0, 10)
        response = call_signal(
            client, script_result=script_result,
            files={'file.txt': unicode_text.encode('utf-8')})
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            unicode_text.encode("utf-8"),
            NodeResult.objects.get_data(node, 'file.txt'))

    def test_signal_stores_multiple_files(self):
        contents = {
            factory.make_string(): factory.make_string().encode('ascii')
            for counter in range(3)}
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        script_result = random.randint(0, 10)
        response = call_signal(
            client, script_result=script_result, files=contents)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            contents,
            {
                result.name: result.data
                for result in node.noderesult_set.all()
            })

    def test_signal_stores_files_up_to_documented_size_limit(self):
        # The documented size limit for commissioning result files:
        # one megabyte.  What happens above this limit is none of
        # anybody's business, but files up to this size should work.
        size_limit = 2 ** 20
        contents = factory.make_string(size_limit, spaces=True)
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        script_result = random.randint(0, 10)
        response = call_signal(
            client, script_result=script_result,
            files={'output.txt': contents.encode('utf-8')})
        self.assertEqual(http.client.OK, response.status_code)
        stored_data = NodeResult.objects.get_data(
            node, 'output.txt')
        self.assertEqual(size_limit, len(stored_data))

    def test_signal_stores_virtual_tag_on_node_if_virtual(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        content = 'qemu'.encode('utf-8')
        response = call_signal(
            client, script_result=0,
            files={'00-maas-02-virtuality.out': content})
        self.assertEqual(http.client.OK, response.status_code)
        node = reload_object(node)
        self.assertEqual(
            ['virtual'], [each_tag.name for each_tag in node.tags.all()])

    def test_signal_removes_virtual_tag_on_node_if_not_virtual(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        tag, _ = Tag.objects.get_or_create(name='virtual')
        node.tags.add(tag)
        client = make_node_client(node=node)
        content = 'none'.encode('utf-8')
        response = call_signal(
            client, script_result=0,
            files={'00-maas-02-virtuality.out': content})
        self.assertEqual(http.client.OK, response.status_code)
        node = reload_object(node)
        self.assertEqual(
            [], [each_tag.name for each_tag in node.tags.all()])

    def test_signal_leaves_untagged_physical_node_unaltered(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        content = 'none'.encode('utf-8')
        response = call_signal(
            client, script_result=0,
            files={'00-maas-02-virtuality.out': content})
        self.assertEqual(http.client.OK, response.status_code)
        node = reload_object(node)
        self.assertEqual(0, len(node.tags.all()))

    def test_signal_current_power_type_mscm_does_not_store_params(self):
        node = factory.make_Node(
            power_type="mscm", status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        params = dict(
            power_address=factory.make_ipv4_address(),
            power_user=factory.make_string(),
            power_pass=factory.make_string())
        with SignalsDisabled("power"):
            response = call_signal(
                client, power_type="moonshot",
                power_parameters=json.dumps(params))
        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        node = reload_object(node)
        self.assertEqual("mscm", node.power_type)
        self.assertNotEqual(params, node.power_parameters)

    def test_signal_refuses_bad_power_type(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        response = call_signal(client, power_type="foo")
        self.expectThat(
            response.status_code,
            Equals(http.client.BAD_REQUEST))
        self.assertThat(
            response.content.decode(settings.DEFAULT_CHARSET),
            Equals("Bad power_type 'foo'"))

    def test_signal_power_type_stores_params(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        params = dict(
            power_address=factory.make_ipv4_address(),
            power_user=factory.make_string(),
            power_pass=factory.make_string())
        response = call_signal(
            client, power_type="ipmi", power_parameters=json.dumps(params))
        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        node = reload_object(node)
        self.assertEqual("ipmi", node.power_type)
        self.assertEqual(params, node.power_parameters)

    def test_signal_power_type_lower_case_works(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        params = dict(
            power_address=factory.make_ipv4_address(),
            power_user=factory.make_string(),
            power_pass=factory.make_string())
        response = call_signal(
            client, power_type="ipmi", power_parameters=json.dumps(params))
        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        node = reload_object(node)
        self.assertEqual(
            params, node.power_parameters)

    def test_signal_invalid_power_parameters(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        response = call_signal(
            client, power_type="ipmi", power_parameters="badjson")
        self.expectThat(
            response.status_code,
            Equals(http.client.BAD_REQUEST))
        self.expectThat(
            response.content.decode(settings.DEFAULT_CHARSET),
            Equals("Failed to parse JSON power_parameters"))

    def test_signal_sets_default_storage_layout_if_OK(self):
        self.patch_autospec(Node, "set_default_storage_layout")
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node)
        response = call_signal(client, status='OK', script_result='0')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(NODE_STATUS.READY, reload_object(node).status)
        self.assertThat(
            Node.set_default_storage_layout,
            MockCalledOnceWith(node))

    def test_signal_does_not_sets_default_storage_layout_if_rack(self):
        self.patch_autospec(Node, "set_default_storage_layout")
        node = factory.make_RackController()
        client = make_node_client(node)
        response = call_signal(client, status='OK', script_result='0')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertThat(
            Node.set_default_storage_layout,
            MockNotCalled())

    def test_signal_does_not_set_default_storage_layout_if_WORKING(self):
        self.patch_autospec(Node, "set_default_storage_layout")
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node)
        response = call_signal(client, status='WORKING', script_result='0')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertThat(
            Node.set_default_storage_layout,
            MockNotCalled())

    def test_signal_does_not_set_default_storage_layout_if_FAILED(self):
        self.patch_autospec(Node, "set_default_storage_layout")
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node)
        response = call_signal(client, status='FAILED', script_result='0')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertThat(
            Node.set_default_storage_layout,
            MockNotCalled())

    def test_signal_calls_sets_initial_network_config_if_OK(self):
        self.useFixture(SignalsDisabled("power"))
        mock_set_initial_networking_configuration = self.patch_autospec(
            Node, "set_initial_networking_configuration")
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node)
        response = call_signal(client, status='OK', script_result='0')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(NODE_STATUS.READY, reload_object(node).status)
        self.assertThat(
            mock_set_initial_networking_configuration,
            MockCalledOnceWith(node))

    def test_signal_doesnt_call_sets_initial_network_config_if_rack(self):
        mock_set_initial_networking_configuration = self.patch_autospec(
            Node, "set_initial_networking_configuration")
        node = factory.make_RackController()
        client = make_node_client(node)
        response = call_signal(client, status='OK', script_result='0')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertThat(
            mock_set_initial_networking_configuration,
            MockNotCalled())

    def test_signal_doesnt_call_sets_initial_network_config_if_WORKING(self):
        mock_set_initial_networking_configuration = self.patch_autospec(
            Node, "set_initial_networking_configuration")
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node)
        response = call_signal(client, status='WORKING', script_result='0')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertThat(
            mock_set_initial_networking_configuration,
            MockNotCalled())

    def test_signal_doesnt_call_sets_initial_network_config_if_FAILED(self):
        mock_set_initial_networking_configuration = self.patch_autospec(
            Node, "set_initial_networking_configuration")
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node)
        response = call_signal(client, status='FAILED', script_result='0')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertThat(
            mock_set_initial_networking_configuration,
            MockNotCalled())


class TestDiskErasingAPI(MAASServerTestCase):

    def setUp(self):
        super(TestDiskErasingAPI, self).setUp()
        self.useFixture(SignalsDisabled("power"))

    def test_signaling_erasing_failure_makes_node_failed_erasing(self):
        node = factory.make_Node(
            status=NODE_STATUS.DISK_ERASING, owner=factory.make_User())
        client = make_node_client(node=node)
        response = call_signal(client, status='FAILED')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.FAILED_DISK_ERASING, reload_object(node).status)

    def test_signaling_erasing_ok_releases_node(self):
        self.patch(Node, "_stop")
        node = factory.make_Node(
            status=NODE_STATUS.DISK_ERASING, owner=factory.make_User(),
            power_state=POWER_STATE.ON, power_type="virsh")
        client = make_node_client(node=node)
        response = call_signal(client, status='OK')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.RELEASING, reload_object(node).status)


class TestRescueModeAPI(MAASServerTestCase):

    def setUp(self):
        super(TestRescueModeAPI, self).setUp()
        self.useFixture(SignalsDisabled("power"))

    def test_signaling_rescue_mode_failure_makes_failed_status(self):
        node = factory.make_Node(
            status=NODE_STATUS.ENTERING_RESCUE_MODE, owner=factory.make_User())
        client = make_node_client(node=node)
        response = call_signal(client, status='FAILED')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.FAILED_ENTERING_RESCUE_MODE,
            reload_object(node).status)

    def test_signaling_entering_rescue_mode_ok_changes_status(self):
        node = factory.make_Node(
            status=NODE_STATUS.ENTERING_RESCUE_MODE, owner=factory.make_User(),
            power_state=POWER_STATE.ON, power_type="virsh")
        client = make_node_client(node=node)
        response = call_signal(client, status='OK')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.RESCUE_MODE, reload_object(node).status)

    def test_signaling_entering_rescue_mode_does_not_set_owner_to_None(self):
        node = factory.make_Node(
            status=NODE_STATUS.ENTERING_RESCUE_MODE, owner=factory.make_User())
        client = make_node_client(node=node)
        response = call_signal(client, status='FAILED')
        self.assertEqual(http.client.OK, response.status_code)
        self.assertIsNotNone(reload_object(node).owner)


class TestByMACMetadataAPI(MAASServerTestCase):

    def test_api_retrieves_node_metadata_by_mac(self):
        node = factory.make_Node_with_Interface_on_Subnet()
        iface = node.get_boot_interface()
        url = reverse(
            'metadata-meta-data-by-mac',
            args=['latest', iface.mac_address, 'instance-id'])
        response = self.client.get(url)
        self.assertEqual(
            (http.client.OK.value,
             iface.node.system_id),
            (response.status_code,
             response.content.decode(settings.DEFAULT_CHARSET)))

    def test_api_retrieves_node_userdata_by_mac(self):
        node = factory.make_Node_with_Interface_on_Subnet(
            status=NODE_STATUS.COMMISSIONING)
        iface = node.get_boot_interface()
        user_data = factory.make_string().encode('ascii')
        NodeUserData.objects.set_user_data(iface.node, user_data)
        url = reverse(
            'metadata-user-data-by-mac', args=['latest', iface.mac_address])
        response = self.client.get(url)
        self.assertEqual(
            (http.client.OK, user_data),
            (response.status_code, response.content))

    def test_api_normally_disallows_anonymous_node_metadata_access(self):
        self.patch(settings, 'ALLOW_UNSAFE_METADATA_ACCESS', False)
        node = factory.make_Node_with_Interface_on_Subnet()
        iface = node.get_boot_interface()
        url = reverse(
            'metadata-meta-data-by-mac',
            args=['latest', iface.mac_address, 'instance-id'])
        response = self.client.get(url)
        self.assertEqual(http.client.FORBIDDEN, response.status_code)


class TestNetbootOperationAPI(MAASServerTestCase):

    def test_netboot_off(self):
        node = factory.make_Node(netboot=True)
        client = make_node_client(node=node)
        url = reverse('metadata-version', args=['latest'])
        response = client.post(url, {'op': 'netboot_off'})
        node = reload_object(node)
        self.assertFalse(node.netboot, response)

    def test_netboot_on(self):
        node = factory.make_Node(netboot=False)
        client = make_node_client(node=node)
        url = reverse('metadata-version', args=['latest'])
        response = client.post(url, {'op': 'netboot_on'})
        node = reload_object(node)
        self.assertTrue(node.netboot, response)


class TestAnonymousAPI(MAASServerTestCase):

    def test_anonymous_netboot_off(self):
        node = factory.make_Node(netboot=True)
        anon_netboot_off_url = reverse(
            'metadata-node-by-id', args=['latest', node.system_id])
        response = self.client.post(
            anon_netboot_off_url, {'op': 'netboot_off'})
        node = reload_object(node)
        self.assertEqual(
            (http.client.OK, False),
            (response.status_code, node.netboot),
            response)

    def test_anonymous_get_enlist_preseed(self):
        # The preseed for enlistment can be obtained anonymously.
        anon_enlist_preseed_url = reverse(
            'metadata-enlist-preseed', args=['latest'])
        # Fake the preseed so we're just exercising the view.
        fake_preseed = factory.make_string()
        self.patch(api, "get_enlist_preseed", Mock(return_value=fake_preseed))
        response = self.client.get(
            anon_enlist_preseed_url, {'op': 'get_enlist_preseed'})
        self.assertEqual(
            (http.client.OK.value,
             "text/plain",
             fake_preseed),
            (response.status_code,
             response["Content-Type"],
             response.content.decode(settings.DEFAULT_CHARSET)),
            response)

    def test_anonymous_get_enlist_preseed_detects_request_origin(self):
        url = 'http://%s' % factory.make_name('host')
        network = IPNetwork("10.1.1/24")
        ip = factory.pick_ip_in_network(network)
        rack = factory.make_RackController(interface=True, url=url)
        nic = rack.get_boot_interface()
        vlan = nic.vlan
        subnet = factory.make_Subnet(cidr=str(network.cidr), vlan=vlan)
        factory.make_StaticIPAddress(subnet=subnet, interface=nic)
        vlan.dhcp_on = True
        vlan.primary_rack = rack
        vlan.save()
        anon_enlist_preseed_url = reverse(
            'metadata-enlist-preseed', args=['latest'])
        response = self.client.get(
            anon_enlist_preseed_url, {'op': 'get_enlist_preseed'},
            REMOTE_ADDR=ip)
        self.assertThat(
            response.content.decode(settings.DEFAULT_CHARSET),
            Contains(url))

    def test_anonymous_get_preseed(self):
        # The preseed for a node can be obtained anonymously.
        node = factory.make_Node()
        anon_node_url = reverse(
            'metadata-node-by-id',
            args=['latest', node.system_id])
        # Fake the preseed so we're just exercising the view.
        fake_preseed = factory.make_string()
        self.patch(api, "get_preseed", lambda node: fake_preseed)
        response = self.client.get(
            anon_node_url, {'op': 'get_preseed'})
        self.assertEqual(
            (http.client.OK.value,
             "text/plain",
             fake_preseed),
            (response.status_code,
             response["Content-Type"],
             response.content.decode(settings.DEFAULT_CHARSET)),
            response)

    def test_anoymous_netboot_off_adds_installation_finished_event(self):
        node = factory.make_Node(netboot=True)
        anon_netboot_off_url = reverse(
            'metadata-node-by-id', args=['latest', node.system_id])
        self.client.post(
            anon_netboot_off_url, {'op': 'netboot_off'})
        latest_event = Event.objects.filter(node=node).last()
        self.assertEqual(
            (
                EVENT_TYPES.NODE_INSTALLATION_FINISHED,
                EVENT_DETAILS[
                    EVENT_TYPES.NODE_INSTALLATION_FINISHED].description,
                "Node disabled netboot",
            ),
            (
                latest_event.type.name,
                latest_event.type.description,
                latest_event.description,
            ))


class TestEnlistViews(MAASServerTestCase):
    """Tests for the enlistment metadata views."""

    def test_get_instance_id(self):
        # instance-id must be available
        md_url = reverse(
            'enlist-metadata-meta-data',
            args=['latest', 'instance-id'])
        response = self.client.get(md_url)
        self.assertEqual(
            (http.client.OK.value, "text/plain"),
            (response.status_code, response["Content-Type"]))
        # just insist content is non-empty. It doesn't matter what it is.
        self.assertTrue(response.content)

    def test_get_hostname(self):
        # instance-id must be available
        md_url = reverse(
            'enlist-metadata-meta-data', args=['latest', 'local-hostname'])
        response = self.client.get(md_url)
        self.assertEqual(
            (http.client.OK, "text/plain"),
            (response.status_code, response["Content-Type"]))
        # just insist content is non-empty. It doesn't matter what it is.
        self.assertTrue(response.content)

    def test_public_keys_returns_empty(self):
        # An enlisting node has no SSH keys, but it does request them.
        # If the node insists, we give it the empty list.
        md_url = reverse(
            'enlist-metadata-meta-data', args=['latest', 'public-keys'])
        response = self.client.get(md_url)
        self.assertEqual(
            (http.client.OK, ""),
            (response.status_code,
             response.content.decode(settings.DEFAULT_CHARSET)))

    def test_metadata_bogus_is_404(self):
        md_url = reverse(
            'enlist-metadata-meta-data',
            args=['latest', 'BOGUS'])
        response = self.client.get(md_url)
        self.assertEqual(http.client.NOT_FOUND, response.status_code)

    def test_get_userdata(self):
        # instance-id must be available
        ud_url = reverse('enlist-metadata-user-data', args=['latest'])
        fake_preseed = factory.make_string()
        self.patch(
            api, "get_enlist_userdata", Mock(return_value=fake_preseed))
        response = self.client.get(ud_url)
        self.assertEqual(
            (http.client.OK, "text/plain", fake_preseed),
            (response.status_code, response["Content-Type"],
             response.content.decode(settings.DEFAULT_CHARSET)),
            response)

    def test_get_userdata_detects_request_origin(self):
        rack_url = 'http://%s' % factory.make_name('host')
        maas_url = factory.make_simple_http_url()
        self.useFixture(RegionConfigurationFixture(maas_url=maas_url))
        network = IPNetwork("10.1.1/24")
        ip = factory.pick_ip_in_network(network)
        rack = factory.make_RackController(interface=True, url=rack_url)
        nic = rack.get_boot_interface()
        vlan = nic.vlan
        subnet = factory.make_Subnet(cidr=str(network.cidr), vlan=vlan)
        factory.make_StaticIPAddress(subnet=subnet, interface=nic)
        vlan.dhcp_on = True
        vlan.primary_rack = rack
        vlan.save()
        url = reverse('enlist-metadata-user-data', args=['latest'])
        response = self.client.get(url, REMOTE_ADDR=ip)
        self.assertThat(
            response.content.decode(settings.DEFAULT_CHARSET),
            MatchesAll(Contains(rack_url), Not(Contains(maas_url))))

    def test_metadata_list(self):
        # /enlist/latest/metadata request should list available keys
        md_url = reverse('enlist-metadata-meta-data', args=['latest', ""])
        response = self.client.get(md_url)
        self.assertEqual(
            (http.client.OK, "text/plain"),
            (response.status_code, response["Content-Type"]))
        self.assertThat(
            response.content.decode(settings.DEFAULT_CHARSET).splitlines(),
            ContainsAll(('instance-id', 'local-hostname')))

    def test_api_version_contents_list(self):
        # top level api (/enlist/latest/) must list 'metadata' and 'userdata'
        md_url = reverse('enlist-version', args=['latest'])
        response = self.client.get(md_url)
        self.assertEqual(
            (http.client.OK, "text/plain"),
            (response.status_code, response["Content-Type"]))
        self.assertThat(
            response.content.decode(settings.DEFAULT_CHARSET).splitlines(),
            ContainsAll(('user-data', 'meta-data')))
