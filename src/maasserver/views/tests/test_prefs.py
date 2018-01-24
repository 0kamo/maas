# Copyright 2012-2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Test maasserver preferences views."""

__all__ = []

import http.client
from unittest.mock import ANY

from apiclient.creds import convert_tuple_to_string
from django.contrib.auth.models import User
from lxml.html import fromstring
from maasserver.enum import ENDPOINT
from maasserver.models.user import get_creds_tuple
from maasserver.testing import get_prefixed_form_data
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.views import prefs as prefs_view
from maastesting.matchers import MockCalledOnceWith
from provisioningserver.events import EVENT_TYPES


class UserPrefsViewTest(MAASServerTestCase):

    def test_prefs_GET_profile(self):
        # The preferences page displays a form with the user's personal
        # information.
        self.client_log_in()
        user = self.logged_in_user
        user.last_name = 'Steve Bam'
        user.save()
        response = self.client.get('/account/prefs/')
        doc = fromstring(response.content)
        self.assertSequenceEqual(
            ['Steve Bam'],
            [elem.value for elem in
                doc.cssselect('input#id_profile-last_name')])

    def test_prefs_GET_api(self):
        # The preferences page displays the API access tokens.
        self.client_log_in()
        user = self.logged_in_user
        # Create a few tokens.
        for _ in range(3):
            user.userprofile.create_authorisation_token()
        response = self.client.get('/account/prefs/')
        doc = fromstring(response.content)
        # The OAuth tokens are displayed.
        for token in user.userprofile.get_authorisation_tokens():
            # The token string is a compact representation of the keys.
            self.assertSequenceEqual(
                [convert_tuple_to_string(get_creds_tuple(token))],
                [elem.value.strip() for elem in
                    doc.cssselect('input#%s' % token.key)])

    def test_prefs_POST_profile(self):
        # The preferences page allows the user the update its profile
        # information.
        self.client_log_in()
        params = {
            'last_name': 'John Doe',
            'email': 'jon@example.com',
        }
        response = self.client.post(
            '/account/prefs/', get_prefixed_form_data('profile', params))

        self.assertEqual(http.client.FOUND, response.status_code)
        user = User.objects.get(id=self.logged_in_user.id)
        self.assertAttributes(user, params)

    def test_prefs_POST_password(self):
        # The preferences page allows the user to change their password.
        mock_create_audit_event = self.patch(prefs_view, 'create_audit_event')
        self.client_log_in()
        self.logged_in_user.set_password('password')
        old_pw = self.logged_in_user.password
        response = self.client.post(
            '/account/prefs/',
            get_prefixed_form_data(
                'password',
                {
                    'old_password': 'test',
                    'new_password1': 'new',
                    'new_password2': 'new',
                }))

        self.assertEqual(http.client.FOUND, response.status_code)
        user = User.objects.get(id=self.logged_in_user.id)
        # The password is SHA1ized, we just make sure that it has changed.
        self.assertNotEqual(old_pw, user.password)
        self.assertThat(mock_create_audit_event, MockCalledOnceWith(
            EVENT_TYPES.AUTHORISATION, ENDPOINT.UI, ANY, None,
            description="Password changed for '%(username)s'."))
