# Copyright 2012-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for `maascli.init`."""

__all__ = []

import os
import tempfile
from unittest.mock import (
    MagicMock,
    patch,
)

from maascli import init
from maascli.parser import ArgumentParser
from maastesting.testcase import MAASTestCase


class TestAddIdmOptions(MAASTestCase):

    def setUp(self):
        super().setUp()
        self.parser = ArgumentParser()
        init.add_idm_options(self.parser)

    def test_add_idm_options_empty(self):
        options = self.parser.parse_args([])
        self.assertIsNone(options.idm_url)
        self.assertIsNone(options.idm_user)
        self.assertIsNone(options.idm_key)
        self.assertIsNone(options.idm_agent_file)

    def test_add_idm_options_idm_url(self):
        options = self.parser.parse_args(
            ['--idm-url', 'http://idm.example.com/'])
        self.assertEqual('http://idm.example.com/', options.idm_url)

    def test_add_idm_options_idm_domain(self):
        options = self.parser.parse_args(
            ['--idm-domain', 'mydomain'])
        self.assertEqual('mydomain', options.idm_domain)

    def test_add_idm_options_idm_user(self):
        options = self.parser.parse_args(['--idm-user', 'my-user'])
        self.assertEqual('my-user', options.idm_user)

    def test_add_idm_options_idm_key(self):
        options = self.parser.parse_args(['--idm-key', 'my-key'])
        self.assertEqual('my-key', options.idm_key)

    def test_add_idm_options_idm_agent_file(self):
        fd, agent_file_name = tempfile.mkstemp()
        self.addCleanup(os.remove, agent_file_name)

        os.write(fd, b'my-agent-file-content')
        os.close(fd)
        options = self.parser.parse_args(['--idm-agent-file', agent_file_name])
        self.assertEqual(
            'my-agent-file-content', options.idm_agent_file.read())

    def test_add_idm_options_idm_admin_group(self):
        options = self.parser.parse_args(
            ['--idm-admin-group', 'admins'])
        self.assertEqual('admins', options.idm_admin_group)


class TestCreateAdminOptions(MAASTestCase):

    def setUp(self):
        super().setUp()
        self.parser = ArgumentParser()
        init.add_create_admin_options(self.parser)

    def test_create_admin_options_empty(self):
        options = self.parser.parse_args([])
        self.assertIsNone(options.admin_username)
        self.assertIsNone(options.admin_password)
        self.assertIsNone(options.admin_email)
        self.assertIsNone(options.admin_ssh_import)

    def test_create_admin_options_username(self):
        options = self.parser.parse_args(
            ['--admin-username', 'my-username'])
        self.assertEqual('my-username', options.admin_username)

    def test_create_admin_options_password(self):
        options = self.parser.parse_args(['--admin-password', 'my-password'])
        self.assertEqual('my-password', options.admin_password)

    def test_create_admin_options_email(self):
        options = self.parser.parse_args(['--admin-email', 'my@example.com'])
        self.assertEqual('my@example.com', options.admin_email)

    def test_create_admin_options_ssh_import(self):
        options = self.parser.parse_args(['--admin-ssh-import', 'lp:me'])
        self.assertEqual('lp:me', options.admin_ssh_import)


class TestCreateAdminAccount(MAASTestCase):

    def setUp(self):
        super().setUp()
        self.parser = ArgumentParser()
        init.add_create_admin_options(self.parser)
        self.mock_call = self.patch(init.subprocess, 'call')
        self.mock_print_msg = self.patch(init, 'print_msg')
        self.maas_region_path = init.get_maas_region_bin_path()

    def test_no_options(self):
        options = self.parser.parse_args([])
        init.create_admin_account(options)
        self.mock_print_msg.assert_called_with('Create first admin account:')
        self.mock_call.assert_called_with(
            [self.maas_region_path, 'createadmin'])

    def test_username(self):
        options = self.parser.parse_args(['--admin-username', 'my-user'])
        init.create_admin_account(options)
        self.mock_print_msg.assert_called_with('Create first admin account:')
        self.mock_call.assert_called_with(
            [self.maas_region_path, 'createadmin', '--username', 'my-user'])

    def test_password(self):
        options = self.parser.parse_args(['--admin-password', 'my-pass'])
        init.create_admin_account(options)
        self.mock_print_msg.assert_called_with('Create first admin account:')
        self.mock_call.assert_called_with(
            [self.maas_region_path, 'createadmin', '--password', 'my-pass'])

    def test_email(self):
        options = self.parser.parse_args(['--admin-email', 'me@example.com'])
        init.create_admin_account(options)
        self.mock_print_msg.assert_called_with('Create first admin account:')
        self.mock_call.assert_called_with(
            [self.maas_region_path,
             'createadmin', '--email', 'me@example.com'])

    def test_ssh_import(self):
        options = self.parser.parse_args(['--admin-ssh-import', 'lp:me'])
        init.create_admin_account(options)
        self.mock_print_msg.assert_called_with('Create first admin account:')
        self.mock_call.assert_called_with(
            [self.maas_region_path, 'createadmin', '--ssh-import', 'lp:me'])

    def test_no_print_header(self):
        options = self.parser.parse_args(
            ['--admin-username', 'my-user', '--admin-password', 'my-pass',
             '--admin-email', 'me@example.com'])
        init.create_admin_account(options)
        self.mock_print_msg.assert_not_called()


class TestConfigureAuthentication(MAASTestCase):

    def setUp(self):
        super().setUp()
        self.maas_bin_path = 'snap-path/bin/maas-region'
        self.mock_subprocess = self.patch(init, 'subprocess')
        self.mock_environ = patch.dict(
            init.os.environ, {'SNAP': 'snap-path'}, clear=True)
        self.mock_environ.start()
        self.parser = ArgumentParser()
        init.add_idm_options(self.parser)

    def tearDown(self):
        self.mock_subprocess.stop()
        self.mock_environ.stop()
        super().tearDown()

    def test_no_options(self):
        options = self.parser.parse_args([])
        init.configure_authentication(options)
        [config_call] = self.mock_subprocess.mock_calls
        method, args, kwargs = config_call
        self.assertEqual('call', method)
        self.assertEqual(([self.maas_bin_path, 'configauth'],), args)
        self.assertEqual({}, kwargs)

    def test_idm_url(self):
        config_auth_args = ['--idm-url', 'http://idm.example.com/']
        options = self.parser.parse_args(config_auth_args)
        init.configure_authentication(options)
        [config_call] = self.mock_subprocess.mock_calls
        method, args, kwargs = config_call
        self.assertEqual('call', method)
        self.assertEqual(
            ([self.maas_bin_path, 'configauth'] + config_auth_args,), args)
        self.assertEqual({}, kwargs)

    def test_idm_user(self):
        config_auth_args = ['--idm-user', 'some-user']
        options = self.parser.parse_args(config_auth_args)
        init.configure_authentication(options)
        [config_call] = self.mock_subprocess.mock_calls
        method, args, kwargs = config_call
        self.assertEqual('call', method)
        self.assertEqual(
            ([self.maas_bin_path, 'configauth'] + config_auth_args,), args)
        self.assertEqual({}, kwargs)

    def test_idm_key(self):
        config_auth_args = ['--idm-key', 'some-key']
        options = self.parser.parse_args(config_auth_args)
        init.configure_authentication(options)
        [config_call] = self.mock_subprocess.mock_calls
        method, args, kwargs = config_call
        self.assertEqual('call', method)
        self.assertEqual(
            ([self.maas_bin_path, 'configauth'] + config_auth_args,), args)
        self.assertEqual({}, kwargs)

    def test_idm_agent_file(self):
        _, agent_file_path = tempfile.mkstemp()
        self.addCleanup(os.remove, agent_file_path)
        config_auth_args = ['--idm-agent-file', agent_file_path]
        options = self.parser.parse_args(config_auth_args)
        init.configure_authentication(options)
        [config_call] = self.mock_subprocess.mock_calls
        method, args, kwargs = config_call
        self.assertEqual('call', method)
        self.assertEqual(
            ([self.maas_bin_path, 'configauth'] + config_auth_args,), args)
        self.assertEqual({}, kwargs)

    def test_full(self):
        _, agent_file = tempfile.mkstemp()
        self.addCleanup(os.remove, agent_file)
        config_auth_args = [
            '--idm-url', 'http://idm.example.com/',
            '--idm-user', 'idm-user',
            '--idm-key', 'idm-key',
            '--idm-agent-file', agent_file]
        options = self.parser.parse_args(config_auth_args)
        init.configure_authentication(options)
        [config_call] = self.mock_subprocess.mock_calls
        method, args, kwargs = config_call
        self.assertEqual('call', method)
        self.assertEqual(
            ([self.maas_bin_path, 'configauth'] + config_auth_args,), args)
        self.assertEqual({}, kwargs)


class TestCreateAccountExternalAuth(MAASTestCase):

    def setUp(self):
        super().setUp()
        self.mock_print_msg = self.patch(init, 'print_msg')
        self.auth_config = {'external_auth_admin_group': 'admins'}
        self.maas_config = {'maas_url': 'http://example.com/MAAS'}

    def mock_bakery_client(self, status_code=200, user_is_admin=True):
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.json.return_value = {
            'id': 20,
            'username': 'user',
            'is_superuser': user_is_admin}
        mock_client = MagicMock()
        mock_client.request.return_value = mock_response
        return mock_client

    def assert_message_printed(self, message):
        [call] = self.mock_print_msg.mock_calls
        self.assertIn(message, call[1][0])

    def test_create_admin(self):
        mock_client = self.mock_bakery_client()
        init.create_account_external_auth(
            self.auth_config, self.maas_config, bakery_client=mock_client)
        mock_client.request.assert_called()
        self.assert_message_printed("Administrator user 'user' created")

    def test_create_not_admin(self):
        mock_client = self.mock_bakery_client(user_is_admin=False)
        init.create_account_external_auth(
            self.auth_config, self.maas_config, bakery_client=mock_client)
        mock_client.request.assert_called()
        self.assert_message_printed(
            "A user with username 'user' has been created, but it's not\n"
            "a superuser")

    def test_request_error_code(self):
        mock_client = self.mock_bakery_client(status_code=500)
        init.create_account_external_auth(
            self.auth_config, self.maas_config, bakery_client=mock_client)
        mock_client.request.assert_called()
        self.assert_message_printed(
            "An error occurred while waiting for the first user creation: "
            "request failed with code 500")

    def test_request_fails(self):
        mock_client = self.mock_bakery_client(status_code=500)
        mock_client.request.side_effect = Exception("something wrong happened")
        init.create_account_external_auth(
            self.auth_config, self.maas_config, bakery_client=mock_client)
        mock_client.request.assert_called()
        self.assert_message_printed(
            "An error occurred while waiting for the first user creation: "
            "something wrong happened")


class TestPrintMsg(MAASTestCase):

    def setUp(self):
        super().setUp()
        self.mock_print = self.patch(init, 'print')

    def test_print_msg_empty_message(self):
        init.print_msg()
        self.mock_print.assert_called_with('', end='\n', flush=True)
