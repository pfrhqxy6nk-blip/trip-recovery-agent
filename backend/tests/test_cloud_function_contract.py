from __future__ import annotations

import ast
from pathlib import Path


def test_cloud_function_forwards_telegram_to_private_worker_boundary() -> None:
    source = Path(__file__).parents[2].joinpath("cloud_function", "main.py").read_text()
    tree = ast.parse(source)
    routes: dict[tuple[str, str], str] | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "ALLOWED_ROUTES"
            for target in node.targets
        ):
            routes = ast.literal_eval(node.value)
            break

    assert routes is not None
    assert routes[("POST", "telegram")] == "/internal/telegram/webhook"
