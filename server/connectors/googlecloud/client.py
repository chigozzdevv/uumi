import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from connectors.base import SecretValue
from connectors.base.errors import ConnectorAuthenticationError, ConnectorError
from connectors.secrets import SecretManagerConnector


@dataclass(frozen=True, slots=True)
class GoogleCloudDiscovery:
    projects: tuple[dict[str, Any], ...]
    access_token: SecretValue
    expires_at: datetime


class GoogleCloudOnboardingConnector:
    def __init__(
        self,
        client_id: str,
        client_secret_reference: str,
        redirect_uri: str,
        secrets: SecretManagerConnector,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret_reference = client_secret_reference
        self._redirect_uri = redirect_uri
        self._secrets = secrets
        self._client = client or httpx.AsyncClient(timeout=30)

    async def discover(self, code: str, verifier: str) -> GoogleCloudDiscovery:
        token, expires_at = await self._exchange(code, verifier)
        try:
            projects = await self._projects(token)
            semaphore = asyncio.Semaphore(5)

            async def inspect(project: dict[str, Any]) -> dict[str, Any] | None:
                try:
                    async with semaphore:
                        services, accounts = await asyncio.gather(
                            self._services(token, project["project_id"]),
                            self._service_accounts(token, project["project_id"]),
                        )
                except ConnectorAuthenticationError:
                    return None
                return {**project, "services": services, "service_accounts": accounts}

            discovered = await asyncio.gather(*(inspect(project) for project in projects))
            return GoogleCloudDiscovery(
                projects=tuple(
                    project
                    for project in discovered
                    if project is not None and project["services"] and project["service_accounts"]
                ),
                access_token=token,
                expires_at=expires_at,
            )
        except Exception:
            token.clear()
            raise

    async def _exchange(self, code: str, verifier: str) -> tuple[SecretValue, datetime]:
        client_secret = await self._secrets.access(self._client_secret_reference)
        try:
            try:
                response = await self._client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": self._client_id,
                        "client_secret": client_secret.bytes().decode(),
                        "code": code,
                        "grant_type": "authorization_code",
                        "redirect_uri": self._redirect_uri,
                        "code_verifier": verifier,
                    },
                )
            except httpx.HTTPError:
                raise ConnectorError(
                    "google-oauth-unavailable",
                    "Google authorization was unavailable",
                    retryable=True,
                ) from None
        finally:
            client_secret.clear()
        if response.status_code != 200:
            raise ConnectorAuthenticationError("Google authorization was rejected")
        value = response.json()
        token = value.get("access_token") if isinstance(value, dict) else None
        expires_in = value.get("expires_in") if isinstance(value, dict) else None
        if not isinstance(token, str) or not token:
            raise ConnectorAuthenticationError("Google returned no access token")
        if not isinstance(expires_in, int) or not 60 <= expires_in <= 86400:
            raise ConnectorAuthenticationError("Google returned an invalid access-token expiry")
        return SecretValue(token.encode()), datetime.now(UTC) + timedelta(seconds=expires_in)

    async def authorize(
        self,
        token: SecretValue,
        project_id: str,
        automation_identity: str,
        runtime_identities: tuple[str, ...],
        broker_service_account: str,
    ) -> None:
        account_access: dict[str, dict[str, set[str]]] = {
            automation_identity: {
                "roles/iam.serviceAccountTokenCreator": {f"serviceAccount:{broker_service_account}"}
            }
        }
        for identity in runtime_identities:
            roles = account_access.setdefault(identity, {})
            roles.setdefault("roles/iam.serviceAccountUser", set()).add(
                f"serviceAccount:{automation_identity}"
            )
            roles.setdefault("roles/iam.serviceAccountTokenCreator", set()).add(
                f"serviceAccount:{broker_service_account}"
            )
        for identity, additions in account_access.items():
            await self._authorize_service_account(token, project_id, identity, additions)
        await self._authorize_project(token, project_id, automation_identity)

    async def _authorize_service_account(
        self,
        token: SecretValue,
        project_id: str,
        identity: str,
        additions: dict[str, set[str]],
    ) -> None:
        resource = f"projects/{project_id}/serviceAccounts/{identity}"
        permissions = await self._post(
            token,
            f"https://iam.googleapis.com/v1/{resource}:testIamPermissions",
            {
                "permissions": [
                    "iam.serviceAccounts.getIamPolicy",
                    "iam.serviceAccounts.setIamPolicy",
                ]
            },
            "The signed-in Google account cannot manage the selected automation identity",
        )
        granted = permissions.get("permissions", [])
        if not isinstance(granted, list) or "iam.serviceAccounts.setIamPolicy" not in granted:
            raise ConnectorAuthenticationError(
                "The signed-in Google account cannot authorize the selected automation identity"
            )
        policy = await self._post(
            token,
            f"https://iam.googleapis.com/v1/{resource}:getIamPolicy",
            {"options": {"requestedPolicyVersion": 3}},
            "The selected automation identity policy could not be read",
        )
        changed = _with_access(policy, additions)
        if changed is policy:
            return
        await self._post(
            token,
            f"https://iam.googleapis.com/v1/{resource}:setIamPolicy",
            {"policy": changed, "updateMask": "bindings,etag,version"},
            "The selected automation identity could not be authorized",
        )

    async def _authorize_project(
        self,
        token: SecretValue,
        project_id: str,
        automation_identity: str,
    ) -> None:
        resource = f"projects/{project_id}"
        permissions = await self._post(
            token,
            f"https://cloudresourcemanager.googleapis.com/v3/{resource}:testIamPermissions",
            {
                "permissions": [
                    "resourcemanager.projects.getIamPolicy",
                    "resourcemanager.projects.setIamPolicy",
                ]
            },
            "The signed-in Google account cannot manage the selected project",
        )
        granted = permissions.get("permissions", [])
        if not isinstance(granted, list) or "resourcemanager.projects.setIamPolicy" not in granted:
            raise ConnectorAuthenticationError(
                "The signed-in Google account cannot authorize the selected project"
            )
        policy = await self._post(
            token,
            f"https://cloudresourcemanager.googleapis.com/v3/{resource}:getIamPolicy",
            {"options": {"requestedPolicyVersion": 3}},
            "The selected project policy could not be read",
        )
        member = f"serviceAccount:{automation_identity}"
        changed = _with_access(
            policy,
            {
                "roles/run.developer": {member},
                "roles/secretmanager.secretVersionManager": {member},
                "roles/secretmanager.viewer": {member},
            },
        )
        if changed is policy:
            return
        await self._post(
            token,
            f"https://cloudresourcemanager.googleapis.com/v3/{resource}:setIamPolicy",
            {"policy": changed, "updateMask": "bindings,etag,version"},
            "The selected project could not be authorized",
        )

    async def _projects(self, token: SecretValue) -> tuple[dict[str, Any], ...]:
        values: list[dict[str, Any]] = []
        page_token = ""
        while len(values) < 100:
            params = {"pageSize": "100"}
            if page_token:
                params["pageToken"] = page_token
            response = await self._get(
                token,
                "https://cloudresourcemanager.googleapis.com/v3/projects:search",
                params,
            )
            projects = response.get("projects", [])
            if not isinstance(projects, list) or not all(
                isinstance(item, dict) for item in projects
            ):
                raise ConnectorError(
                    "google-projects-invalid", "Google returned invalid project metadata"
                )
            for project in projects:
                project_id = project.get("projectId")
                name = project.get("name")
                display_name = project.get("displayName")
                state = project.get("state")
                if (
                    isinstance(project_id, str)
                    and isinstance(name, str)
                    and name.startswith("projects/")
                    and name.removeprefix("projects/").isdigit()
                    and isinstance(display_name, str)
                    and display_name
                    and state == "ACTIVE"
                ):
                    values.append(
                        {
                            "project_id": project_id,
                            "project_number": name.removeprefix("projects/"),
                            "display_name": display_name,
                        }
                    )
            next_token = response.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                return tuple(values)
            page_token = next_token
        raise ConnectorError(
            "google-projects-limit",
            "Google Cloud onboarding supports at most 100 visible projects",
        )

    async def _services(self, token: SecretValue, project_id: str) -> tuple[dict[str, Any], ...]:
        response = await self._get(
            token,
            f"https://run.googleapis.com/apis/serving.knative.dev/v1/namespaces/{project_id}/services",
            {"limit": "1000"},
            unavailable=f"Cloud Run services could not be read for {project_id}",
        )
        items = response.get("items", [])
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ConnectorError(
                "google-services-invalid", "Google returned invalid Cloud Run metadata"
            )
        services: list[dict[str, Any]] = []
        for item in items:
            metadata = item.get("metadata")
            spec = item.get("spec")
            template = spec.get("template") if isinstance(spec, dict) else None
            template_spec = template.get("spec") if isinstance(template, dict) else None
            labels = metadata.get("labels") if isinstance(metadata, dict) else None
            name = metadata.get("name") if isinstance(metadata, dict) else None
            region = (
                labels.get("cloud.googleapis.com/location") if isinstance(labels, dict) else None
            )
            identity = (
                template_spec.get("serviceAccountName") if isinstance(template_spec, dict) else None
            )
            if isinstance(name, str) and isinstance(region, str):
                services.append(
                    {
                        "reference": f"projects/{project_id}/locations/{region}/services/{name}",
                        "display_name": name,
                        "region": region,
                        "runtime_identity": identity if isinstance(identity, str) else None,
                    }
                )
        return tuple(sorted(services, key=lambda item: (item["region"], item["display_name"])))

    async def _service_accounts(
        self, token: SecretValue, project_id: str
    ) -> tuple[dict[str, str], ...]:
        accounts: list[dict[str, str]] = []
        page_token = ""
        while True:
            params = {"pageSize": "100"}
            if page_token:
                params["pageToken"] = page_token
            response = await self._get(
                token,
                f"https://iam.googleapis.com/v1/projects/{project_id}/serviceAccounts",
                params,
                unavailable=f"Service accounts could not be read for {project_id}",
            )
            values = response.get("accounts", [])
            if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
                raise ConnectorError(
                    "google-identities-invalid", "Google returned invalid service accounts"
                )
            for value in values:
                email = value.get("email")
                display_name = value.get("displayName")
                disabled = value.get("disabled", False)
                if isinstance(email, str) and not disabled:
                    accounts.append(
                        {
                            "email": email,
                            "display_name": (
                                display_name
                                if isinstance(display_name, str) and display_name
                                else email
                            ),
                        }
                    )
            next_token = response.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                return tuple(sorted(accounts, key=lambda item: item["display_name"].lower()))
            page_token = next_token

    async def _get(
        self,
        token: SecretValue,
        url: str,
        params: dict[str, str],
        unavailable: str = "Google Cloud discovery was unavailable",
    ) -> dict[str, Any]:
        try:
            response = await self._client.get(
                url,
                headers={
                    "Authorization": f"Bearer {token.bytes().decode()}",
                    "Accept": "application/json",
                },
                params=params,
            )
        except httpx.HTTPError:
            raise ConnectorError(
                "google-discovery-unavailable", unavailable, retryable=True
            ) from None
        if response.status_code in {401, 403}:
            raise ConnectorAuthenticationError(unavailable)
        if response.status_code != 200:
            raise ConnectorError(
                "google-discovery-error",
                f"{unavailable} (HTTP {response.status_code})",
                retryable=response.status_code in {408, 429, 500, 502, 503, 504},
            )
        value = response.json()
        if not isinstance(value, dict):
            raise ConnectorError(
                "google-discovery-invalid", "Google returned a non-object response"
            )
        return value

    async def _post(
        self,
        token: SecretValue,
        url: str,
        body: dict[str, Any],
        unavailable: str,
    ) -> dict[str, Any]:
        try:
            response = await self._client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token.bytes().decode()}",
                    "Accept": "application/json",
                },
                json=body,
            )
        except httpx.HTTPError:
            raise ConnectorError(
                "google-authorization-unavailable", unavailable, retryable=True
            ) from None
        if response.status_code in {401, 403}:
            raise ConnectorAuthenticationError(unavailable)
        if response.status_code != 200:
            raise ConnectorError(
                "google-authorization-error",
                f"{unavailable} (HTTP {response.status_code})",
                retryable=response.status_code in {408, 409, 429, 500, 502, 503, 504},
            )
        value = response.json()
        if not isinstance(value, dict):
            raise ConnectorError(
                "google-authorization-invalid", "Google returned a non-object response"
            )
        return value


def _with_access(
    policy: dict[str, Any],
    additions: dict[str, set[str]],
) -> dict[str, Any]:
    raw_bindings = policy.get("bindings", [])
    if not isinstance(raw_bindings, list) or not all(
        isinstance(binding, dict) for binding in raw_bindings
    ):
        raise ConnectorError(
            "google-iam-policy-invalid", "Google returned an invalid IAM policy"
        )
    bindings = [dict(binding) for binding in raw_bindings]
    changed_policy = False
    for role, members_to_add in additions.items():
        target = next(
            (
                binding
                for binding in bindings
                if binding.get("role") == role and not binding.get("condition")
            ),
            None,
        )
        if target is None:
            bindings.append({"role": role, "members": sorted(members_to_add)})
            changed_policy = True
            continue
        members = target.get("members", [])
        if not isinstance(members, list) or not all(isinstance(value, str) for value in members):
            raise ConnectorError(
                "google-iam-policy-invalid", "Google returned an invalid IAM policy"
            )
        merged = sorted(set(members) | members_to_add)
        if merged != members:
            target["members"] = merged
            changed_policy = True
    if not changed_policy:
        return policy
    changed = {
        key: value for key, value in policy.items() if key in {"auditConfigs", "etag", "version"}
    }
    changed["bindings"] = bindings
    if any(binding.get("condition") for binding in bindings):
        changed["version"] = 3
    return changed
