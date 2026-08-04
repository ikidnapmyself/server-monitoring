"""Configure the active intelligence provider (an AI provider or the local fallback).

Interactive:
    python manage.py setup_intelligence
Non-interactive:
    python manage.py setup_intelligence --provider openai --api-key sk-... --model gpt-4o
    python manage.py setup_intelligence --provider local
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Configure the active intelligence provider (AI provider or local fallback)."

    def add_arguments(self, parser):
        parser.add_argument("--provider", help="Provider key (e.g. openai, claude, local).")
        parser.add_argument("--api-key", dest="api_key", help="API key (AI providers only).")
        parser.add_argument("--model", help="Model name (defaults to the provider's default).")

    def handle(self, *args, **options):
        from apps.intelligence.models import IntelligenceProvider
        from apps.intelligence.providers import PROVIDERS

        provider = options.get("provider") or self._prompt_provider(list(PROVIDERS))
        if provider not in PROVIDERS:
            raise CommandError(f"Unknown provider '{provider}'. Choices: {', '.join(PROVIDERS)}.")

        config: dict = {}
        if provider != "local":
            default_model = getattr(PROVIDERS[provider], "default_model", "")
            # Interactive only when the key wasn't supplied as a flag — then we also
            # prompt for the model. With --api-key, the model defaults silently.
            api_key = options.get("api_key")
            interactive = api_key is None
            if interactive:
                api_key = input(f"{provider.capitalize()} API key: ").strip()
            if not api_key:
                raise CommandError(f"Provider '{provider}' requires an API key.")
            model = options.get("model")
            if not model:
                if interactive:
                    model = input(f"{provider.capitalize()} model [{default_model}]: ").strip()
                model = model or default_model
            config = {"api_key": api_key, "model": model}

        # update_or_create + the model's single-active save() invariant ensure exactly
        # one active provider.
        record, _created = IntelligenceProvider.objects.update_or_create(
            name=f"setup-{provider}",
            defaults={
                "provider": provider,
                "config": config,
                "is_active": True,
                "description": "[setup_intelligence] configured provider",
            },
        )

        detail = "" if provider == "local" else f", model={config.get('model')}"
        self.stdout.write(
            self.style.SUCCESS(f"Intelligence provider active: {record.name} ({provider}){detail}")
        )

    def _prompt_provider(self, names: list[str]) -> str:
        self.stdout.write("Available providers:")
        for i, name in enumerate(names, 1):
            self.stdout.write(f"  {i}) {name}")
        while True:
            choice = input(f"Select [1-{len(names)}]: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(names):
                return names[int(choice) - 1]
