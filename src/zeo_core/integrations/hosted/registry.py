"""Explicit, reviewed hosted service registrations; nothing is discovered.

A registration names an in-process proxy class for one service identity. There
is no import-by-name, entry-point scan or URL: the proxy still reaches the
provider only as a named operation through the fixed-origin hosted client.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from zeo_core.integrations.hosted.client import HostedConnectionClient
from zeo_core.integrations.hosted.profile import ServiceRequirement
from zeo_core.integrations.hosted.services import (
    HostedGoogleDriveService,
    HostedServiceBinding,
)
from zeo_core.integrations.hosted.transport import ZEOCONNECT_PROTOCOL_VERSION


class HostedServiceFactory(Protocol):
    def __call__(
        self, *, client: HostedConnectionClient, binding: HostedServiceBinding
    ) -> object: ...


@dataclass(frozen=True)
class HostedServiceRegistration:
    """One service identity, the exact operations its proxy implements."""

    service: str
    operations: frozenset[str]
    factory: HostedServiceFactory
    resource_bound_operations: frozenset[str] = frozenset()
    protocol_version: str = ZEOCONNECT_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        # Reuse the one reviewed identity rule instead of restating it here.
        ServiceRequirement(service=self.service, operations=tuple(self.operations))
        if not self.resource_bound_operations <= self.operations:
            raise ValueError("resource-bound operations must be registered operations")
        if self.protocol_version != ZEOCONNECT_PROTOCOL_VERSION:
            raise ValueError("registration targets an unsupported member protocol")


class HostedServiceRegistry:
    """Immutable after construction: a host composes it, code cannot extend it."""

    def __init__(self, registrations: Iterable[HostedServiceRegistration]) -> None:
        by_service: dict[str, HostedServiceRegistration] = {}
        for registration in registrations:
            if registration.service in by_service:
                raise ValueError(
                    f"duplicate hosted service registration: {registration.service}"
                )
            by_service[registration.service] = registration
        self._by_service = by_service

    @property
    def services(self) -> frozenset[str]:
        return frozenset(self._by_service)

    def requires_selected_resource(self, requirement: ServiceRequirement) -> bool:
        registration = self._by_service.get(requirement.service)
        return registration is not None and bool(
            registration.resource_bound_operations.intersection(requirement.operations)
        )

    def build(
        self,
        requirement: ServiceRequirement,
        *,
        client: HostedConnectionClient,
        binding: HostedServiceBinding,
    ) -> object | None:
        """Return a proxy only when every requested operation is registered."""

        registration = self._by_service.get(requirement.service)
        if registration is None or not registration.operations.issuperset(
            requirement.operations
        ):
            return None
        return registration.factory(client=client, binding=binding)


def _gemini_images(
    *, client: HostedConnectionClient, binding: HostedServiceBinding
) -> object:
    from zeo_core.integrations.imaging.hosted import HostedGeminiImages

    return HostedGeminiImages(client=client, binding=binding)


def _llm_chat(
    *, client: HostedConnectionClient, binding: HostedServiceBinding
) -> object:
    from zeo_core.integrations.llms.hosted import HostedLLMChat

    return HostedLLMChat(client=client, binding=binding)


def _recraft_images(
    *, client: HostedConnectionClient, binding: HostedServiceBinding
) -> object:
    from zeo_core.integrations.imaging.hosted import HostedRecraftImages

    return HostedRecraftImages(client=client, binding=binding)


# What the resolver offered before this registry existed, plus the billed image
# services of the proposed contract 1.3.0 (ZEOCORE-SOW-12). Adding a service
# here is a reviewed change to what the hosted profile can resolve.
REVIEWED_HOSTED_SERVICES = HostedServiceRegistry(
    (
        HostedServiceRegistration(
            service="google.drive",
            operations=frozenset({"google.drive.file.download"}),
            factory=HostedGoogleDriveService,
            resource_bound_operations=frozenset({"google.drive.file.download"}),
        ),
        HostedServiceRegistration(
            service="gemini.image",
            operations=frozenset({"gemini.image.generate"}),
            factory=_gemini_images,
        ),
        HostedServiceRegistration(
            service="recraft",
            operations=frozenset(
                {
                    "recraft.image.generate",
                    "recraft.image.image_to_image",
                    "recraft.image.remove_background",
                    "recraft.image.crisp_upscale",
                    "recraft.image.vectorize",
                    "recraft.account.read",
                }
            ),
            factory=_recraft_images,
        ),
        # Billed LLM chat (proposed contract, draft 3; ZEOCORE-SOW-13).
        HostedServiceRegistration(
            service="openai",
            operations=frozenset(
                {"openai.responses.create", "openai.chat.completions.create"}
            ),
            factory=_llm_chat,
        ),
        HostedServiceRegistration(
            service="anthropic",
            operations=frozenset({"anthropic.messages.create"}),
            factory=_llm_chat,
        ),
        HostedServiceRegistration(
            service="nebius",
            operations=frozenset({"nebius.chat.completions.create"}),
            factory=_llm_chat,
        ),
    )
)

__all__ = [
    "REVIEWED_HOSTED_SERVICES",
    "HostedServiceFactory",
    "HostedServiceRegistration",
    "HostedServiceRegistry",
]
