# Copyright 2012-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""UserProfile model."""

__all__ = [
    'UserProfile',
    ]


from django.contrib.auth.models import User
from django.db.models import (
    Manager,
    Model,
    OneToOneField,
)
from django.shortcuts import get_object_or_404
from maasserver import DefaultMeta
from maasserver.exceptions import CannotDeleteUserException
from maasserver.models.cleansave import CleanSave
from piston3.models import Token


class UserProfileManager(Manager):
    """A utility to manage the collection of `UserProfile` (or `User`).

    Use this when dealing with users that represent real-world users.  System
    users do not have `UserProfile` objects attached to them.
    """

    def all_users(self):
        """Returns all "real" users (i.e. not including system users).

        :return: A QuerySet of the users.
        :rtype: django.db.models.query.QuerySet_

        .. _django.db.models.query.QuerySet: https://docs.djangoproject.com/
           en/dev/ref/models/querysets/

        """
        user_ids = UserProfile.objects.all().values_list('user', flat=True)
        return User.objects.filter(id__in=user_ids)


class UserProfile(CleanSave, Model):
    """A User profile to store MAAS specific methods and fields.

    :ivar user: The related User_.

    .. _User: https://docs.djangoproject.com/
       en/dev/topics/auth/
       #storing-additional-information-about-users

    """

    class Meta(DefaultMeta):
        """Needed for South to recognize this model."""

    objects = UserProfileManager()
    user = OneToOneField(User)

    def delete(self):
        if self.user.node_set.exists():
            nb_nodes = self.user.node_set.count()
            msg = (
                "User %s cannot be deleted: %d node(s) are still "
                "allocated to this user." %
                (self.user.username, nb_nodes))
            raise CannotDeleteUserException(msg)
        if self.user.filestorage_set.exists():
            self.user.filestorage_set.all().delete()
        self.user.consumers.all().delete()
        self.user.delete()
        super(UserProfile, self).delete()

    def get_authorisation_tokens(self):
        """Fetches all the user's OAuth tokens.

        :return: A QuerySet of the tokens.
        :rtype: django.db.models.query.QuerySet_

        .. _django.db.models.query.QuerySet: https://docs.djangoproject.com/
           en/dev/ref/models/querysets/

        """
        # Avoid circular imports.
        from maasserver.models.user import get_auth_tokens

        return get_auth_tokens(self.user)

    def create_authorisation_token(self):
        """Create a new Token and its related Consumer (OAuth authorisation).

        :return: A tuple containing the Consumer and the Token that were
            created.
        :rtype: tuple

        """
        # Avoid circular imports.
        from maasserver.models.user import create_auth_token

        token = create_auth_token(self.user)
        return token.consumer, token

    def delete_authorisation_token(self, token_key):
        """Delete the user's OAuth token wich key token_key.

        :param token_key: The key of the token to be deleted.
        :type token_key: string
        :raises: `django.http.Http404`

        """
        token = get_object_or_404(
            Token, user=self.user, token_type=Token.ACCESS, key=token_key)
        token.consumer.delete()
        token.delete()

    def __str__(self):
        return self.user.username
