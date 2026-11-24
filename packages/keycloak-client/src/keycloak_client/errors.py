class AuthenticationError(Exception):
    """Raised when authentication fails."""

    pass


class KeycloakAdminError(Exception):
    """Raised when a Keycloak Admin API operation fails."""

    pass
