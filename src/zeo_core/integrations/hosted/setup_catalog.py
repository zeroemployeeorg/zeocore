"""Reviewed, inert setup dispositions for every existing integration track.

Local implementation is not live account qualification. Hosted directions remain
unadmitted until their deployed product and provider evidence is recorded.
No adapter is imported, initialized, probed, or authorized by this catalogue.
"""

from __future__ import annotations

from typing import Literal

from ..environments.catalog import CATALOG
from .profile import ExecutionProfile, ServiceRequirement
from .setup import (
    AuthMethod,
    OperationSetup,
    ProfileSetup,
    SetupAction,
    SetupManifest,
    SupportStatus,
)

_REVIEWED = "2026-09-11"
_SOURCE = "zeocore@4fb6dc9cbc7ad54b47ca74614f4f34c740a573e1"


def _operation(service: str, operation: str, version: str | None) -> OperationSetup:
    return OperationSetup(
        operation=operation,
        capability=f"{operation}@{version}" if version else None,
        requirement=ServiceRequirement(service=service, operations=(operation,)),
    )


_OPERATIONS = {
    "hubspot.marketing": tuple(
        _operation("hubspot.marketing", "hubspot.marketing." + name, "1.0.0")
        for name in (
            "read",
            "email.save",
            "email.clone",
            "email.publish",
            "email.cancel",
            "campaign.save",
            "campaign.asset",
            "subscription.update",
            "workflow.save",
            "workflow.enrollment",
            "archive",
        )
    ),
    "kit.marketing": tuple(
        _operation("kit.marketing", "kit.marketing." + name, "1.0.0")
        for name in (
            "read",
            "broadcast.save",
            "broadcast.send",
            "sequence.save",
            "sequence.email.save",
            "sequence.email.publish",
            "sequence.state",
            "sequence.enroll",
            "subscriber.create",
            "subscriber.unsubscribe",
            "tag.save",
            "tag.membership",
            "delete",
        )
    ),
    "github": (_operation("github", "github.repository.file.read", "1.1.0"),),
    "google.calendar": (
        _operation("google.calendar", "google.calendar.event.create", "1.0.0"),
    ),
    "google.drive": (_operation("google.drive", "google.drive.file.download", None),),
    "google.docs": (_operation("google.docs", "google.docs.document.read", None),),
    "social.bluesky": (_operation("bluesky.post", "bluesky.post.create", None),),
}


def _manifest(
    integration: str,
    display: str,
    *,
    purpose: str,
    access: str,
    account: str,
    selection: str,
    prerequisite: str,
    hosted_auth: AuthMethod,
    local_auth: AuthMethod = AuthMethod.GUIDED_SECRET,
    surface: Literal[
        "business", "builder", "model", "local", "onboarding"
    ] = "business",
) -> SetupManifest:
    local_tool = surface == "local"
    onboarding = surface == "onboarding"
    local_limit = (
        "Installed binary/environment and local permissions require an explicit probe."
        if local_tool
        else (
            "Local SDK exists; configuration, account access and live"
            " behavior require verification."
        )
    )
    return SetupManifest(
        integration_id=integration,
        display_name=display,
        purpose=purpose,
        requested_access=access,
        account_label=account,
        resource_selection=selection,
        custody=(
            "Local permissions stay on this Mac; no hosted account credential."
            if local_tool
            else (
                "Admitted hosted accounts use Broker custody; local SDK "
                "configuration stays with the host."
            )
        ),
        repair=(
            "Recheck installation and local permissions; preserve the initiating work."
            if local_tool
            else (
                "Reauthenticate the same verified account; an identity "
                "change requires review."
            )
        ),
        revocation=(
            "Remove the local permission in system settings."
            if local_tool
            else (
                "Revoke app access or the account connection explicitly; "
                "provider-accepted work may finish."
            )
        ),
        prerequisites=(prerequisite,),
        profiles=(
            ProfileSetup(
                profile=ExecutionProfile.LOCAL,
                support=SupportStatus.IMPLEMENTED,
                auth_method=local_auth,
                auth_revision="local-core-0.11.0",
                limitations=(local_limit,),
                actions=(SetupAction.OPEN_LOCAL_SETTINGS,),
            ),
            *(
                ProfileSetup(
                    profile=profile,
                    support=(
                        SupportStatus.UNSUPPORTED
                        if local_tool
                        else SupportStatus.NOT_ADMITTED
                    ),
                    auth_method=hosted_auth,
                    auth_revision="unadmitted-setup-direction-1",
                    limitations=(
                        (
                            "This hosted profile has not passed its connection, "
                            "provider and Runtime admission gates."
                        ),
                    ),
                    actions=(),
                )
                for profile in (ExecutionProfile.HOSTED, ExecutionProfile.GOVERNED)
            ),
        ),
        operations=_OPERATIONS.get(integration, ()),
        unmapped_operations=(
            "Only the listed existing named operations are mapped; "
            "other local SDK methods are not admitted hosted "
            "operations."
        ),
        identity_probe=(
            "Explicit local installation inspection."
            if local_tool
            else (
                "Explicit authenticated account identity inspection; "
                "labels alone are not identity."
            )
        ),
        health_probe=(
            "Explicit binary/environment and permission check."
            if local_tool
            else (
                "Check the requested operation's scopes, provider "
                "features and selected resources independently."
            )
        ),
        guide=CATALOG[integration].guide,
        source_revision=_SOURCE,
        reviewed_on=_REVIEWED,
        owner="ZEOconnect" if onboarding else "Zeocore",
        surface=surface,
    )


SETUP_CATALOGUE: tuple[SetupManifest, ...] = (
    _manifest(
        "hubspot.marketing",
        "HubSpot",
        purpose="Prepare and send your newsletter.",
        access=(
            "Reviewed marketing email operations; consent alone does "
            "not authorize sending."
        ),
        account="HubSpot portal",
        selection="Choose sender, template, subscription type and audience separately.",
        prerequisite=(
            "Qualify the actual portal plan, scopes, sender/domain "
            "and subscription eligibility before sending."
        ),
        hosted_auth=AuthMethod.OAUTH,
    ),
    _manifest(
        "revolut.business",
        "Revolut Business",
        purpose=(
            "Import business bank transactions, expenses and their receipts "
            "as expense evidence."
        ),
        access=(
            "Reviewed read operations only: accounts, bounded transaction and "
            "expense pages, single expenses, bounded receipt downloads and "
            "labels; no payment, transfer or settings change."
        ),
        account="Revolut Business account",
        selection="Choose the exact accounts whose transactions may be read.",
        prerequisite=(
            "Assisted setup in every profile: register that profile's own "
            "public certificate in Revolut Business settings, then give "
            "consent. Profiles are selected explicitly and never fall back."
        ),
        hosted_auth=AuthMethod.OAUTH,
        local_auth=AuthMethod.OAUTH,
    ),
    _manifest(
        "kit.marketing",
        "Kit",
        purpose="Prepare broadcasts and email sequences.",
        access="Reviewed broadcast, sequence and subscriber operations only.",
        account="Kit account",
        selection="Review the broadcast or sequence and its exact audience.",
        prerequisite=(
            "Hosted OAuth eligibility and live K1/K2/K3 send evidence"
            " remain required; local personal keys are separate."
        ),
        hosted_auth=AuthMethod.OAUTH,
    ),
    *(
        _manifest(
            "google." + name,
            "Google " + label,
            purpose=purpose,
            access=(
                "Consent is specific to this service and reviewed "
                "operations; sign-in does not grant business access."
            ),
            account="Google account",
            selection=selection,
            prerequisite=(
                "Verify the service's actual granted scopes. Selected-"
                "file onboarding cannot expose a provider token or "
                "silently widen scope."
            ),
            hosted_auth=AuthMethod.OAUTH,
            local_auth=AuthMethod.OAUTH,
        )
        for name, label, purpose, selection in (
            (
                "mail",
                "Mail",
                "Read and prepare email for admitted work.",
                "Choose the reviewed mailbox and message scope.",
            ),
            (
                "drive",
                "Drive",
                "Use selected files for your work.",
                (
                    "Select exact files; a folder does not imply recursive or"
                    " future access."
                ),
            ),
            (
                "calendar",
                "Calendar",
                "Work with reviewed calendar events.",
                "Choose a calendar and explicit event operations.",
            ),
            (
                "docs",
                "Docs",
                "Use selected documents.",
                "Select exact documents available under the granted profile.",
            ),
            (
                "sheets",
                "Sheets",
                "Work with selected spreadsheets.",
                "Choose the spreadsheet and admitted ranges/operations.",
            ),
            (
                "slides",
                "Slides",
                "Work with selected presentations.",
                "Choose the presentation and admitted operations.",
            ),
        )
    ),
    _manifest(
        "notion",
        "Notion",
        purpose="Use selected workspace pages in your work.",
        access="Reviewed workspace, page and database operations.",
        account="Notion workspace",
        selection=(
            "Select explicit pages/databases; disclose dynamic "
            "subtree access separately."
        ),
        prerequisite=(
            "Public OAuth and actual page access need hosted "
            "admission and live verification."
        ),
        hosted_auth=AuthMethod.OAUTH,
    ),
    _manifest(
        "github",
        "GitHub",
        purpose="Use selected repositories for builder work.",
        access="Reviewed repository operations; no blanket write access.",
        account="GitHub installation",
        selection="Choose repositories within the installation's actual permissions.",
        prerequisite=(
            "Admit a ZEO GitHub App installation and its exact "
            "permissions before hosted use."
        ),
        hosted_auth=AuthMethod.INSTALLATION,
        surface="builder",
    ),
    _manifest(
        "social.bluesky",
        "Bluesky",
        purpose="Prepare and publish an explicitly approved post.",
        access="Reviewed post creation; enrollment never publishes.",
        account="Bluesky account",
        selection="Verify the account identity and exact post before approval.",
        prerequisite=(
            "Existing hosted app-password adapter needs live "
            "admission; evaluate official OAuth before broad rollout."
        ),
        hosted_auth=AuthMethod.GUIDED_SECRET,
    ),
    _manifest(
        "supabase",
        "Supabase",
        purpose="Connect an explicitly selected builder project.",
        access=(
            "Project operations constrained by the selected credential and app policy."
        ),
        account="Supabase project",
        selection="Select project and reviewed data operations.",
        prerequisite=(
            "Advanced builder setup only; never request ZEO service "
            "database credentials."
        ),
        hosted_auth=AuthMethod.UNSUPPORTED,
        surface="builder",
    ),
    _manifest(
        "llms",
        "Model access",
        purpose="Configure separately approved model processing.",
        access=(
            "Provider-specific model API access, separate from "
            "business-account consent."
        ),
        account="Model provider account",
        selection="Choose the provider/model and approved data destination.",
        prerequisite=(
            "Verify API entitlement and billing; consumer "
            "subscriptions do not imply API access. Local models "
            "require separate support checks."
        ),
        hosted_auth=AuthMethod.GUIDED_SECRET,
        surface="model",
    ),
    _manifest(
        "gemini.images",
        "Gemini images",
        purpose="Generate images for admitted work.",
        access="Image-service API access; generic Google consent does not grant it.",
        account="Image service account",
        selection="Choose approved source artifacts and image destination.",
        prerequisite=(
            "Verify image-service availability, credentials and billing separately."
        ),
        hosted_auth=AuthMethod.GUIDED_SECRET,
        surface="model",
    ),
    *(
        _manifest(
            name,
            label,
            purpose=purpose,
            access="Local tool and selected file permissions.",
            account="This Mac",
            selection="Use only the approved input and output files.",
            prerequisite=(
                "Installer-managed binary and its required engines must "
                "pass an explicit local probe."
            ),
            hosted_auth=AuthMethod.UNSUPPORTED,
            local_auth=AuthMethod.LOCAL_TOOL,
            surface="local",
        )
        for name, label, purpose in (
            ("pandoc", "Pandoc", "Convert local documents."),
            ("ffmpeg", "FFmpeg", "Process local audio and video."),
            ("jupytext", "Jupytext", "Convert local notebook source formats."),
        )
    ),
    _manifest(
        "notebook",
        "Notebook execution",
        purpose="Run an approved local notebook.",
        access="Managed local execution and selected files.",
        account="This Mac",
        selection="Bind approved input snapshots and output locations.",
        prerequisite=(
            "Verify the managed environment, kernel and file "
            "permissions; this is not an OAuth connection."
        ),
        hosted_auth=AuthMethod.UNSUPPORTED,
        local_auth=AuthMethod.LOCAL_PERMISSION,
        surface="local",
    ),
    _manifest(
        "zeoconnect",
        "Zeo sign-in",
        purpose="Sign in and pair this Mac with your organization.",
        access="Human identity and installation pairing; business consent is separate.",
        account="Zeo organization",
        selection=(
            "Choose your organization and installation; no provider account is implied."
        ),
        prerequisite=(
            "Verified identity, entitlement and separate Runtime "
            "issuer enrollment are required."
        ),
        hosted_auth=AuthMethod.DEVICE,
        local_auth=AuthMethod.DEVICE,
        surface="onboarding",
    ),
)


def setup_manifest(integration_id: str) -> SetupManifest | None:
    """A metadata lookup; unknown integrations never become a generic OAuth form."""
    return next(
        (item for item in SETUP_CATALOGUE if item.integration_id == integration_id),
        None,
    )
