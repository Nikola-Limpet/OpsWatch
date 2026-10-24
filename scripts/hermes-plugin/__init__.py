"""Hermes plugin: route registered commands to Opswatch's private owner chat."""

COMMANDS = {
    "opsstatus": ("Application and monitor status", ""),
    "incidents": ("Open Opswatch incidents", ""),
    "incident": ("Incident evidence", "OW-0001"),
    "diagnose": ("Agent fix suggestion", "OW-0001"),
    "queries": ("Measured slow database queries", ""),
    "opsmodel": ("Show or change diagnosis model", "MODEL_ID"),
    "opshelp": ("Opswatch command reference", ""),
    "opsask": ("Ask about monitor evidence", "QUESTION"),
    "fix": ("Repair an incident and prepare a draft PR", "OW-0001"),
    "repairs": ("Repair job progress and PRs", ""),
    "repair": ("Repair job details", "RF-0001"),
    "cancelfix": ("Cancel a queued or running repair", "RF-0001"),
    "repos": ("Repositories available for repairs", ""),
}


def register(ctx):
    for name, (description, args_hint) in COMMANDS.items():
        ctx.register_command(
            name,
            handler=lambda raw_args: "Use this command in Telegram.",
            description=description,
            args_hint=args_hint,
        )

    from gateway.platform_registry import platform_registry

    from .handler import install_callbacks

    entry = platform_registry.get("telegram")
    if entry is None:
        raise RuntimeError("Opswatch buttons require the Hermes Telegram platform")
    original_factory = entry.adapter_factory
    if not getattr(original_factory, "_opswatch_buttons", False):

        def factory(config):
            adapter = original_factory(config)
            install_callbacks(type(adapter))
            return adapter

        factory._opswatch_buttons = True
        entry.adapter_factory = factory
