# Copyright 2013-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for the commissioning-related portions of the MAAS API."""

__all__ = []

from base64 import b64encode
import http.client
from itertools import chain
import random

from django.core.urlresolvers import reverse
from maasserver.testing.api import APITestCase
from maasserver.testing.factory import factory
from maasserver.testing.matchers import HasStatusCode
from maasserver.utils.converters import json_load_bytes
from maasserver.utils.orm import reload_object
from maastesting.utils import sample_binary_data
from metadataserver.enum import (
    SCRIPT_STATUS,
    SCRIPT_STATUS_CHOICES,
    SCRIPT_TYPE,
)
from metadataserver.fields import Bin
from metadataserver.models import CommissioningScript


class AdminCommissioningScriptsAPITest(APITestCase.ForAdmin):
    """Tests for `CommissioningScriptsHandler`."""

    def get_url(self):
        return reverse('commissioning_scripts_handler')

    def test_GET_lists_commissioning_scripts(self):
        # Use lower-case names.  The database and the test may use
        # different collation orders with different ideas about case
        # sensitivity.
        names = {factory.make_name('script').lower() for counter in range(5)}
        for name in names:
            factory.make_CommissioningScript(name=name)

        response = self.client.get(self.get_url())

        self.assertEqual(
            (http.client.OK, sorted(names)),
            (response.status_code, json_load_bytes(response.content)))

    def test_POST_creates_commissioning_script(self):
        # This uses Piston's built-in POST code, so there are no tests for
        # corner cases (like "script already exists") here.
        name = factory.make_name('script')
        content = factory.make_bytes()

        # Every uploaded file also has a name.  But this is completely
        # unrelated to the name we give to the commissioning script.
        response = self.client.post(
            self.get_url(),
            {
                'name': name,
                'content': factory.make_file_upload(content=content),
            })
        self.assertThat(response, HasStatusCode(http.client.OK))

        returned_script = json_load_bytes(response.content)
        self.assertEqual(
            (name, b64encode(content).decode("ascii")),
            (returned_script['name'], returned_script['content']))

        stored_script = CommissioningScript.objects.get(name=name)
        self.assertEqual(content, stored_script.content)


class CommissioningScriptsAPITest(APITestCase.ForUser):

    def get_url(self):
        return reverse('commissioning_scripts_handler')

    def test_GET_is_forbidden(self):
        response = self.client.get(self.get_url())
        self.assertThat(response, HasStatusCode(http.client.FORBIDDEN))

    def test_POST_is_forbidden(self):
        response = self.client.post(
            self.get_url(),
            {'name': factory.make_name('script')})
        self.assertThat(response, HasStatusCode(http.client.FORBIDDEN))


class AdminCommissioningScriptAPITest(APITestCase.ForAdmin):
    """Tests for `CommissioningScriptHandler`."""

    def get_url(self, script_name):
        return reverse('commissioning_script_handler', args=[script_name])

    def test_GET_returns_script_contents(self):
        script = factory.make_CommissioningScript()
        response = self.client.get(self.get_url(script.name))
        self.assertThat(response, HasStatusCode(http.client.OK))
        self.assertEqual(script.content, response.content)

    def test_GET_preserves_binary_data(self):
        script = factory.make_CommissioningScript(content=sample_binary_data)
        response = self.client.get(self.get_url(script.name))
        self.assertThat(response, HasStatusCode(http.client.OK))
        self.assertEqual(sample_binary_data, response.content)

    def test_PUT_updates_contents(self):
        old_content = b'old:%s' % factory.make_string().encode('ascii')
        script = factory.make_CommissioningScript(content=old_content)
        new_content = b'new:%s' % factory.make_string().encode('ascii')

        response = self.client.put(
            self.get_url(script.name),
            {'content': factory.make_file_upload(content=new_content)})
        self.assertThat(response, HasStatusCode(http.client.OK))

        self.assertEqual(new_content, reload_object(script).content)

    def test_DELETE_deletes_script(self):
        script = factory.make_CommissioningScript()
        self.client.delete(self.get_url(script.name))
        self.assertItemsEqual(
            [],
            CommissioningScript.objects.filter(name=script.name))


class CommissioningScriptAPITest(APITestCase.ForUser):

    def get_url(self, script_name):
        return reverse('commissioning_script_handler', args=[script_name])

    def test_GET_is_forbidden(self):
        # It's not inconceivable that commissioning scripts contain
        # credentials of some sort.  There is no need for regular users
        # (consumers of the MAAS) to see these.
        script = factory.make_CommissioningScript()
        response = self.client.get(self.get_url(script.name))
        self.assertThat(response, HasStatusCode(http.client.FORBIDDEN))

    def test_PUT_is_forbidden(self):
        script = factory.make_CommissioningScript()
        response = self.client.put(
            self.get_url(script.name), {'content': factory.make_string()})
        self.assertThat(response, HasStatusCode(http.client.FORBIDDEN))

    def test_DELETE_is_forbidden(self):
        script = factory.make_CommissioningScript()
        response = self.client.put(self.get_url(script.name))
        self.assertThat(response, HasStatusCode(http.client.FORBIDDEN))


class NodeCommissionResultHandlerAPITest(APITestCase.ForUser):

    def store_result(
            self, script_result, stdout=None, stderr=None,
            **kwargs):
        # Create a test store_result which doesn't run the script hooks.
        if stdout is not None:
            script_result.stdout = Bin(stdout.encode('utf-8'))
        if stderr is not None:
            script_result.stderr = Bin(stderr.encode('utf-8'))
        for arg, value in kwargs.items():
            setattr(script_result, arg, value)
        if script_result.exit_status == 0:
            script_result.status = SCRIPT_STATUS.PASSED
        else:
            script_result.status = SCRIPT_STATUS.FAILED
        script_result.save()

    def test_list_returns_expected_fields(self):
        node = factory.make_Node(with_empty_script_sets=True)
        for script_set in (
                node.current_commissioning_script_set,
                node.current_testing_script_set,
                node.current_installation_script_set):
            for script_result in script_set:
                self.store_result(
                    script_result, exit_status=0, stdout=factory.make_string())

        script_set = node.current_commissioning_script_set
        script_result = script_set.scriptresult_set.first()

        url = reverse('node_results_handler')
        response = self.client.get(url, {'system_id': [node.system_id]})
        self.assertThat(response, HasStatusCode(http.client.OK))
        parsed_results = json_load_bytes(response.content)
        for parsed_result in parsed_results:
            if parsed_result['name'] == script_result.name:
                break
        self.assertEquals(
            node.current_commissioning_script_set.scriptresult_set.count() +
            node.current_testing_script_set.scriptresult_set.count() +
            node.current_installation_script_set.scriptresult_set.count(),
            len(parsed_results))
        self.assertItemsEqual([
            'created', 'updated', 'id', 'name', 'script_result', 'result_type',
            'node', 'data'], parsed_result.keys())
        self.assertEquals(script_result.id, parsed_result['id'])
        self.assertEquals(script_result.name, parsed_result['name'])
        self.assertEquals(
            script_result.exit_status, parsed_result['script_result'])
        self.assertEquals(script_set.result_type, parsed_result['result_type'])
        self.assertEquals(
            {'system_id': script_set.node.system_id},
            parsed_result['node'])
        self.assertEquals(
            script_result.stdout.decode('utf-8'), parsed_result['data'])

    def test_list_returns_with_multiple_empty_data(self):
        node = factory.make_Node(with_empty_script_sets=True)
        for script_result in chain(
                node.current_commissioning_script_set,
                node.current_testing_script_set,
                node.current_installation_script_set):
            self.store_result(
                script_result, exit_status=0, stdout='', stderr='')

        url = reverse('node_results_handler')
        response = self.client.get(url)
        self.assertThat(response, HasStatusCode(http.client.OK))
        parsed_results = json_load_bytes(response.content)
        self.assertItemsEqual(
            [
                script_result.stdout.decode('utf-8')
                for script_result in chain(
                    node.current_commissioning_script_set,
                    node.current_testing_script_set,
                    node.current_installation_script_set)
            ],
            [parsed_result.get('data') for parsed_result in parsed_results])

    def test_list_returns_stderr(self):
        node = factory.make_Node(with_empty_script_sets=True)
        for script_result in chain(
                node.current_commissioning_script_set,
                node.current_testing_script_set,
                node.current_installation_script_set):
            self.store_result(
                script_result, exit_status=0, stdout='', stderr='')

        url = reverse('node_results_handler')
        response = self.client.get(url, {'system_id': [node.system_id]})
        self.assertThat(response, HasStatusCode(http.client.OK))
        parsed_results = json_load_bytes(response.content)
        self.assertItemsEqual(
            [
                script_result.stdout.decode('utf-8')
                for script_result in chain(
                    node.current_commissioning_script_set,
                    node.current_testing_script_set,
                    node.current_installation_script_set)
            ],
            [parsed_result['data'] for parsed_result in parsed_results])

    def test_list_shows_all_latest_results(self):
        # XXX ltrager 2017-01-23 - Needed until builtin tests are added.
        factory.make_Script(script_type=SCRIPT_TYPE.TESTING)
        node = factory.make_Node(with_empty_script_sets=True)
        script_results = []
        for script_result in node.current_commissioning_script_set:
            self.store_result(script_result, exit_status=0)
            script_results.append(script_result)

        for script_result in node.current_testing_script_set:
            self.store_result(script_result, exit_status=0)
            script_results.append(script_result)

        for script_result in node.current_installation_script_set:
            script_result.store_result(exit_status=0)
            script_results.append(script_result)

        url = reverse('node_results_handler')
        response = self.client.get(url, {'system_id': [node.system_id]})
        self.assertThat(response, HasStatusCode(http.client.OK))
        parsed_results = json_load_bytes(response.content)
        self.assertItemsEqual(
            {script_result.id for script_result in script_results},
            {parsed_result['id'] for parsed_result in parsed_results})

    def test_list_can_be_filtered_by_name(self):
        node = factory.make_Node(with_empty_script_sets=True)
        script_results = []
        for script_result in node.current_commissioning_script_set:
            self.store_result(script_result, exit_status=0)
            script_results.append(script_result)
        script_result = random.choice(script_results)

        url = reverse('node_results_handler')
        response = self.client.get(url, {'name': script_result.name})
        self.assertThat(response, HasStatusCode(http.client.OK))
        parsed_results = json_load_bytes(response.content)
        self.assertEquals(script_result.id, parsed_results[0]['id'])

    def test_list_displays_only_visible_nodes(self):
        node = factory.make_Node(
            owner=factory.make_User(), with_empty_script_sets=True)
        for script_result in node.current_commissioning_script_set:
            self.store_result(script_result, exit_status=0)

        url = reverse('node_results_handler')
        response = self.client.get(url)
        self.assertThat(response, HasStatusCode(http.client.OK))
        parsed_results = json_load_bytes(response.content)
        self.assertEqual([], parsed_results)

    def test_list_displays_all_results(self):
        script_results = []
        for _ in range(3):
            node = factory.make_Node(with_empty_script_sets=True)
            for script_result in node.current_commissioning_script_set:
                self.store_result(script_result, exit_status=0)
                script_results.append(script_result)
            for script_result in node.current_testing_script_set:
                self.store_result(script_result, exit_status=0)
                script_results.append(script_result)
            for script_result in node.current_installation_script_set:
                self.store_result(script_result, exit_status=0)
                script_results.append(script_result)

        url = reverse('node_results_handler')
        response = self.client.get(url)
        self.assertThat(response, HasStatusCode(http.client.OK))
        parsed_results = json_load_bytes(response.content)
        self.assertItemsEqual(
            {script_result.id for script_result in script_results},
            {parsed_result['id'] for parsed_result in parsed_results})

    def test_list_only_displayed_completed_results(self):
        node = factory.make_Node(with_empty_script_sets=True)
        expected_results = []
        for script_set in (
                node.current_commissioning_script_set,
                node.current_testing_script_set,
                node.current_installation_script_set):
            for status, _ in SCRIPT_STATUS_CHOICES:
                script_result = factory.make_ScriptResult(
                    script_set=script_set, status=status)
                if status in (
                        SCRIPT_STATUS.PASSED, SCRIPT_STATUS.FAILED,
                        SCRIPT_STATUS.TIMEOUT):
                    expected_results.append(script_result)

        url = reverse('node_results_handler')
        response = self.client.get(url)
        self.assertThat(response, HasStatusCode(http.client.OK))
        parsed_results = json_load_bytes(response.content)
        self.assertItemsEqual(
            {script_result.id for script_result in expected_results},
            {parsed_result['id'] for parsed_result in parsed_results})
