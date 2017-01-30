# Copyright 2015-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for the metadata progress reporting API."""

__all__ = []

import base64
import bz2
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.urlresolvers import reverse
from maasserver.enum import NODE_STATUS
from maasserver.models import (
    Event,
    Tag,
)
from maasserver.models.signals.testing import SignalsDisabled
from maasserver.testing.factory import factory
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.testing.testclient import MAASSensibleOAuthClient
from maasserver.utils.orm import reload_object
from maastesting.matchers import MockNotCalled
from metadataserver import api
from metadataserver.enum import SCRIPT_STATUS
from metadataserver.models import NodeKey
from metadataserver.nodeinituser import get_node_init_user
from provisioningserver.utils import typed


def make_node_client(node=None):
    """Create a test client logged in as if it were `node`."""
    if node is None:
        node = factory.make_Node()
    token = NodeKey.objects.get_token_for_node(node)
    return MAASSensibleOAuthClient(get_node_init_user(), token)


@typed
def encode_as_base64(content: bytes) -> str:
    return base64.encodebytes(content).decode("ascii")


def call_status(client=None, node=None, payload=None):
    """Call the API's status endpoint.

    The API does not receive any form data, just a JSON encoding several
    values.
    """
    if node is None:
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
    if client is None:
        client = make_node_client(node)

    url = reverse('metadata-status', args=[node.system_id])

    return client.post(
        url, content_type='application/json', data=json.dumps(payload))


class TestStatusAPI(MAASServerTestCase):

    def setUp(self):
        super(TestStatusAPI, self).setUp()
        self.useFixture(SignalsDisabled("power"))

    def test_other_user_than_node_cannot_signal_installation_result(self):
        node = factory.make_Node(status=NODE_STATUS.DEPLOYING)
        client = MAASSensibleOAuthClient(factory.make_User())
        response = call_status(client, node)
        self.assertEqual(http.client.FORBIDDEN, response.status_code)
        self.assertEqual(
            NODE_STATUS.DEPLOYING, reload_object(node).status)
        # No node events were logged.
        self.assertFalse(Event.objects.filter(node=node).exists())

    def test_status_installation_result_does_not_affect_other_node(self):
        node1 = factory.make_Node(status=NODE_STATUS.DEPLOYING)
        node2 = factory.make_Node(status=NODE_STATUS.DEPLOYING)
        client = make_node_client(node1)
        payload = {
            'event_type': 'finish',
            'result': 'SUCCESS',
            'origin': 'curtin',
            'name': 'cmd-install',
            'description': 'Command Install',
        }
        response = call_status(client, node1, payload)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.DEPLOYING, reload_object(node2).status)
        # Check last node1 event.
        self.assertEqual(
            "'curtin' Command Install",
            Event.objects.filter(node=node1).last().description)
        # There must me no events for node2.
        self.assertFalse(Event.objects.filter(node=node2).exists())

    def test_status_installation_success_leaves_node_deploying(self):
        node = factory.make_Node(interface=True, status=NODE_STATUS.DEPLOYING)
        client = make_node_client(node=node)
        payload = {
            'event_type': 'finish',
            'result': 'SUCCESS',
            'origin': 'curtin',
            'name': 'cmd-install',
            'description': 'Command Install',
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(NODE_STATUS.DEPLOYING, reload_object(node).status)
        # Check last node event.
        self.assertEqual(
            "'curtin' Command Install",
            Event.objects.filter(node=node).last().description)

    def test_status_with_non_json_payload_fails(self):
        node = factory.make_Node(interface=True, status=NODE_STATUS.DEPLOYING)
        client = make_node_client(node=node)
        payload = {
            'event_type': 'finish',
            'result': 'SUCCESS',
            'origin': 'curtin',
            'name': 'cmd-install',
            'description': 'Command Install',
        }
        client = make_node_client(node)
        url = reverse('metadata-status', args=[node.system_id])
        response = client.post(
            url, content_type='application/json',
            data=urllib.parse.urlencode(payload))
        self.assertEqual(http.client.BAD_REQUEST, response.status_code)

    def test_status_commissioning_failure_leaves_node_failed(self):
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        payload = {
            'event_type': 'finish',
            'result': 'FAILURE',
            'origin': 'curtin',
            'name': 'commissioning',
            'description': 'Commissioning',
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.FAILED_COMMISSIONING, reload_object(node).status)
        # Check last node event.
        self.assertEqual(
            "'curtin' Commissioning",
            Event.objects.filter(node=node).last().description)

    def test_status_commissioning_failure_clears_owner(self):
        user = factory.make_User()
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.COMMISSIONING, owner=user)
        client = make_node_client(node=node)
        payload = {
            'event_type': 'finish',
            'result': 'FAILURE',
            'origin': 'curtin',
            'name': 'commissioning',
            'description': 'Commissioning',
        }
        self.assertEqual(user, node.owner)  # Node has an owner
        response = call_status(client, node, payload)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.FAILED_COMMISSIONING, reload_object(node).status)
        self.assertIsNone(reload_object(node).owner)

    def test_status_installation_failure_leaves_node_failed(self):
        node = factory.make_Node(interface=True, status=NODE_STATUS.DEPLOYING)
        client = make_node_client(node=node)
        payload = {
            'event_type': 'finish',
            'result': 'FAILURE',
            'origin': 'curtin',
            'name': 'cmd-install',
            'description': 'Command Install',
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.FAILED_DEPLOYMENT, reload_object(node).status)
        # Check last node event.
        self.assertEqual(
            "Installation failed (refer to the installation"
            " log for more information).",
            Event.objects.filter(node=node).last().description)

    def test_status_installation_fail_leaves_node_failed(self):
        node = factory.make_Node(interface=True, status=NODE_STATUS.DEPLOYING)
        client = make_node_client(node=node)
        payload = {
            'event_type': 'finish',
            'result': 'FAIL',
            'origin': 'curtin',
            'name': 'cmd-install',
            'description': 'Command Install',
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.FAILED_DEPLOYMENT, reload_object(node).status)
        # Check last node event.
        self.assertEqual(
            "Installation failed (refer to the installation"
            " log for more information).",
            Event.objects.filter(node=node).last().description)

    def test_status_installation_failure_doesnt_clear_owner(self):
        user = factory.make_User()
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.DEPLOYING, owner=user)
        client = make_node_client(node=node)
        payload = {
            'event_type': 'finish',
            'result': 'FAILURE',
            'origin': 'curtin',
            'name': 'cmd-install',
            'description': 'Command Install',
        }
        self.assertEqual(user, node.owner)  # Node has an owner
        response = call_status(client, node, payload)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.FAILED_DEPLOYMENT, reload_object(node).status)
        self.assertIsNotNone(reload_object(node).owner)

    def test_status_commissioning_failure_does_not_populate_tags(self):
        populate_tags_for_single_node = self.patch(
            api, "populate_tags_for_single_node")
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        payload = {
            'event_type': 'finish',
            'result': 'FAILURE',
            'origin': 'curtin',
            'name': 'commissioning',
            'description': 'Commissioning',
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.FAILED_COMMISSIONING, reload_object(node).status)
        self.assertThat(populate_tags_for_single_node, MockNotCalled())

    def test_status_erasure_failure_leaves_node_failed(self):
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.DISK_ERASING)
        client = make_node_client(node=node)
        payload = {
            'event_type': 'finish',
            'result': 'FAILURE',
            'origin': 'curtin',
            'name': 'cmd-erase',
            'description': 'Erasing disk',
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.FAILED_DISK_ERASING, reload_object(node).status)
        # Check last node event.
        self.assertEqual(
            "Failed to erase disks.",
            Event.objects.filter(node=node).last().description)

    def test_status_erasure_failure_does_not_populate_tags(self):
        populate_tags_for_single_node = self.patch(
            api, "populate_tags_for_single_node")
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.DISK_ERASING)
        client = make_node_client(node=node)
        payload = {
            'event_type': 'finish',
            'result': 'FAILURE',
            'origin': 'curtin',
            'name': 'cmd-erase',
            'description': 'Erasing disk',
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.FAILED_DISK_ERASING, reload_object(node).status)
        self.assertThat(populate_tags_for_single_node, MockNotCalled())

    def test_status_erasure_failure_doesnt_clear_owner(self):
        user = factory.make_User()
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.DISK_ERASING, owner=user)
        client = make_node_client(node=node)
        payload = {
            'event_type': 'finish',
            'result': 'FAILURE',
            'origin': 'curtin',
            'name': 'cmd-erase',
            'description': 'Erasing disk',
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(
            NODE_STATUS.FAILED_DISK_ERASING, reload_object(node).status)
        self.assertEqual(user, node.owner)

    def test_status_with_file_bad_encoder_fails(self):
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        contents = b'These are the contents of the file.'
        encoded_content = encode_as_base64(bz2.compress(contents))
        payload = {
            'event_type': 'finish',
            'result': 'FAILURE',
            'origin': 'curtin',
            'name': 'commissioning',
            'description': 'Commissioning',
            'files': [
                {
                    "path": "sample.txt",
                    "encoding": "uuencode",
                    "compression": "bzip2",
                    "content": encoded_content
                }
            ]
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.BAD_REQUEST, response.status_code)
        self.assertEqual(
            'Invalid encoding: uuencode',
            response.content.decode(settings.DEFAULT_CHARSET))

    def test_status_with_file_bad_compression_fails(self):
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.COMMISSIONING)
        client = make_node_client(node=node)
        contents = b'These are the contents of the file.'
        encoded_content = encode_as_base64(bz2.compress(contents))
        payload = {
            'event_type': 'finish',
            'result': 'FAILURE',
            'origin': 'curtin',
            'name': 'commissioning',
            'description': 'Commissioning',
            'files': [
                {
                    "path": "sample.txt",
                    "encoding": "base64",
                    "compression": "jpeg",
                    "content": encoded_content
                }
            ]
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.BAD_REQUEST, response.status_code)
        self.assertEqual(
            'Invalid compression: jpeg',
            response.content.decode(settings.DEFAULT_CHARSET))

    def test_status_with_file_no_compression_succeeds(self):
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.COMMISSIONING,
            with_empty_script_sets=True)
        script_result = (
            node.current_commissioning_script_set.scriptresult_set.first())
        script_result.status = SCRIPT_STATUS.RUNNING
        script_result.save()
        client = make_node_client(node=node)
        contents = b'These are the contents of the file.'
        encoded_content = encode_as_base64(contents)
        payload = {
            'event_type': 'finish',
            'result': 'FAILURE',
            'origin': 'curtin',
            'name': 'commissioning',
            'description': 'Commissioning',
            'files': [
                {
                    "path": script_result.name,
                    "encoding": "base64",
                    "content": encoded_content
                }
            ]
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(contents, reload_object(script_result).stdout)

    def test_status_with_file_invalid_statuses_fails(self):
        """Adding files should fail for every status that's neither
        COMMISSIONING nor DEPLOYING"""
        for node_status in [
                NODE_STATUS.DEFAULT,
                NODE_STATUS.NEW,
                NODE_STATUS.FAILED_COMMISSIONING,
                NODE_STATUS.MISSING,
                NODE_STATUS.READY,
                NODE_STATUS.RESERVED,
                NODE_STATUS.DEPLOYED,
                NODE_STATUS.RETIRED,
                NODE_STATUS.BROKEN,
                NODE_STATUS.ALLOCATED,
                NODE_STATUS.FAILED_DEPLOYMENT,
                NODE_STATUS.RELEASING,
                NODE_STATUS.FAILED_RELEASING,
                NODE_STATUS.DISK_ERASING,
                NODE_STATUS.FAILED_DISK_ERASING]:
            node = factory.make_Node(interface=True, status=node_status)
            client = make_node_client(node=node)
            contents = b'These are the contents of the file.'
            encoded_content = encode_as_base64(bz2.compress(contents))
            payload = {
                'event_type': 'finish',
                'result': 'FAILURE',
                'origin': 'curtin',
                'name': 'commissioning',
                'description': 'Commissioning',
                'files': [
                    {
                        "path": "sample.txt",
                        "encoding": "base64",
                        "compression": "bzip2",
                        "content": encoded_content
                    }
                ]
            }
            response = call_status(client, node, payload)
            self.assertEqual(http.client.BAD_REQUEST, response.status_code)
            self.assertEqual(
                'Invalid status for saving files: %d' % node_status,
                response.content.decode(settings.DEFAULT_CHARSET))

    def test_status_with_file_succeeds(self):
        """Adding files should succeed for every status that's either
        COMMISSIONING or DEPLOYING"""
        for node_status, target_status in [
                (NODE_STATUS.COMMISSIONING, NODE_STATUS.FAILED_COMMISSIONING),
                (NODE_STATUS.DEPLOYING, NODE_STATUS.FAILED_DEPLOYMENT)]:
            node = factory.make_Node(
                interface=True, status=node_status,
                with_empty_script_sets=True)
            if node_status == NODE_STATUS.COMMISSIONING:
                script_set = node.current_commissioning_script_set
            elif node_status == NODE_STATUS.DEPLOYING:
                script_set = node.current_installation_script_set
            script_result = script_set.scriptresult_set.first()
            script_result.status = SCRIPT_STATUS.RUNNING
            script_result.save()
            client = make_node_client(node=node)
            contents = b'These are the contents of the file.'
            encoded_content = encode_as_base64(bz2.compress(contents))
            payload = {
                'event_type': 'finish',
                'result': 'FAILURE',
                'origin': 'curtin',
                'name': 'commissioning',
                'description': 'Commissioning',
                'files': [
                    {
                        "path": script_result.name,
                        "encoding": "base64",
                        "compression": "bzip2",
                        "content": encoded_content
                    }
                ]
            }
            response = call_status(client, node, payload)
            self.assertEqual(http.client.OK, response.status_code)
            self.assertEqual(
                target_status, reload_object(node).status)
            # Check the node result.
            self.assertEqual(contents, reload_object(script_result).stdout)

    def test_status_with_results_succeeds(self):
        """Adding a script result should succeed"""
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.COMMISSIONING,
            with_empty_script_sets=True)
        script_result = (
            node.current_commissioning_script_set.scriptresult_set.first())
        script_result.status = SCRIPT_STATUS.RUNNING
        script_result.save()
        client = make_node_client(node=node)
        contents = b'These are the contents of the file.'
        encoded_content = encode_as_base64(bz2.compress(contents))
        payload = {
            'event_type': 'finish',
            'result': 'FAILURE',
            'origin': 'curtin',
            'name': 'commissioning',
            'description': 'Commissioning',
            'files': [
                {
                    "path": script_result.name,
                    "encoding": "base64",
                    "compression": "bzip2",
                    "content": encoded_content,
                    "result": -42
                }
            ]
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.OK, response.status_code)
        script_result = reload_object(script_result)
        self.assertEqual(contents, script_result.stdout)
        self.assertEqual(-42, script_result.exit_status)

    def test_status_with_results_no_exit_status_defaults_to_zero(self):
        """Adding a script result should succeed without a return code defaults
        it to zero."""
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.COMMISSIONING,
            with_empty_script_sets=True)
        script_result = (
            node.current_commissioning_script_set.scriptresult_set.first())
        script_result.status = SCRIPT_STATUS.RUNNING
        script_result.save()
        client = make_node_client(node=node)
        contents = b'These are the contents of the file.'
        encoded_content = encode_as_base64(bz2.compress(contents))
        payload = {
            'event_type': 'finish',
            'result': 'FAILURE',
            'origin': 'curtin',
            'name': 'commissioning',
            'description': 'Commissioning',
            'files': [
                {
                    "path": script_result.name,
                    "encoding": "base64",
                    "compression": "bzip2",
                    "content": encoded_content,
                }
            ]
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(0, reload_object(script_result).exit_status)

    def test_status_with_missing_event_type_fails(self):
        node = factory.make_Node(interface=True, status=NODE_STATUS.DEPLOYING)
        client = make_node_client(node=node)
        payload = {
            'result': 'SUCCESS',
            'origin': 'curtin',
            'name': 'cmd-install',
            'description': 'Command Install',
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.BAD_REQUEST, response.status_code)
        self.assertIn(
            'Missing parameter in status message',
            response.content.decode(settings.DEFAULT_CHARSET))

    def test_status_with_missing_origin_fails(self):
        node = factory.make_Node(interface=True, status=NODE_STATUS.DEPLOYING)
        client = make_node_client(node=node)
        payload = {
            'event_type': 'finish',
            'result': 'SUCCESS',
            'name': 'cmd-install',
            'description': 'Command Install',
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.BAD_REQUEST, response.status_code)
        self.assertIn(
            'Missing parameter in status message',
            response.content.decode(settings.DEFAULT_CHARSET))

    def test_status_with_missing_name_fails(self):
        node = factory.make_Node(interface=True, status=NODE_STATUS.DEPLOYING)
        client = make_node_client(node=node)
        payload = {
            'event_type': 'finish',
            'result': 'SUCCESS',
            'origin': 'curtin',
            'description': 'Command Install',
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.BAD_REQUEST, response.status_code)
        self.assertIn(
            'Missing parameter in status message',
            response.content.decode(settings.DEFAULT_CHARSET))

    def test_status_with_missing_description_fails(self):
        node = factory.make_Node(interface=True, status=NODE_STATUS.DEPLOYING)
        client = make_node_client(node=node)
        payload = {
            'event_type': 'finish',
            'result': 'SUCCESS',
            'origin': 'curtin',
            'name': 'cmd-install',
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.BAD_REQUEST, response.status_code)
        self.assertIn(
            'Missing parameter in status message',
            response.content.decode(settings.DEFAULT_CHARSET))

    def test_status_stores_virtual_tag_on_node_if_virtual(self):
        node = factory.make_Node(
            status=NODE_STATUS.COMMISSIONING, with_empty_script_sets=True)
        client = make_node_client(node=node)
        content = 'virtual'.encode('utf-8')
        payload = {
            'event_type': 'finish',
            'result': 'SUCCESS',
            'origin': 'curtin',
            'name': 'commissioning',
            'description': 'Commissioning',
            'files': [
                {
                    "path": "00-maas-02-virtuality",
                    "encoding": "base64",
                    "content": encode_as_base64(content),
                }
            ]
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.OK, response.status_code)
        node = reload_object(node)
        self.assertEqual(
            ["virtual"], [each_tag.name for each_tag in node.tags.all()])
        for script_result in node.current_commissioning_script_set:
            if script_result.name == "00-maas-02-virtuality":
                break
        self.assertEqual(content, script_result.stdout)

    def test_status_removes_virtual_tag_on_node_if_not_virtual(self):
        node = factory.make_Node(
            status=NODE_STATUS.COMMISSIONING, with_empty_script_sets=True)
        tag, _ = Tag.objects.get_or_create(name='virtual')
        node.tags.add(tag)
        client = make_node_client(node=node)
        content = 'none'.encode('utf-8')
        payload = {
            'event_type': 'finish',
            'result': 'SUCCESS',
            'origin': 'curtin',
            'name': 'commissioning',
            'description': 'Commissioning',
            'files': [
                {
                    "path": "00-maas-02-virtuality",
                    "encoding": "base64",
                    "content": encode_as_base64(content),
                }
            ]
        }
        response = call_status(client, node, payload)
        self.assertEqual(http.client.OK, response.status_code)
        node = reload_object(node)
        self.assertEqual(
            [], [each_tag.name for each_tag in node.tags.all()])
        for script_result in node.current_commissioning_script_set:
            if script_result.name == "00-maas-02-virtuality":
                break
        self.assertEqual(content, script_result.stdout)

    def test_status_updates_script_status_last_ping(self):
        nodes = {
            status: factory.make_Node(
                status=status, with_empty_script_sets=True)
            for status in (
                NODE_STATUS.COMMISSIONING,
                NODE_STATUS.TESTING,
                NODE_STATUS.DEPLOYING)
        }

        for status, node in nodes.items():
            client = make_node_client(node=node)
            payload = {
                'event_type': 'progress',
                'origin': 'curtin',
                'name': 'test',
                'description': 'testing',
            }
            response = call_status(client, node, payload)
            self.assertEqual(http.client.OK, response.status_code)
            script_set_statuses = {
                NODE_STATUS.COMMISSIONING: (
                    node.current_commissioning_script_set),
                NODE_STATUS.TESTING: node.current_testing_script_set,
                NODE_STATUS.DEPLOYING: node.current_installation_script_set,
            }
            script_set = script_set_statuses.get(node.status)
            self.assertIsNotNone(script_set.last_ping)
