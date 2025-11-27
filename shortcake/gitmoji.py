"""Gitmoji data and picker for commit messages."""

from dataclasses import dataclass

from rich_toolkit.menu import Menu, Option


@dataclass
class Gitmoji:
    """A gitmoji entry."""

    emoji: str
    code: str
    description: str


# Gitmoji list from https://gitmoji.dev
# Note: Some emojis have their variation selector (U+FE0F) removed to work around
# a Rich bug where it miscalculates width of narrow characters + VS-16.
# This affects: ♻ ✏ 🗃 🏗 ⚗ 🏷 🗑 ⚰ ⬇ ⬆
GITMOJIS: list[Gitmoji] = [
    Gitmoji("🎨", ":art:", "Improve structure / format of the code"),
    Gitmoji("⚡️", ":zap:", "Improve performance"),
    Gitmoji("🔥", ":fire:", "Remove code or files"),
    Gitmoji("🐛", ":bug:", "Fix a bug"),
    Gitmoji("🚑️", ":ambulance:", "Critical hotfix"),
    Gitmoji("✨", ":sparkles:", "Introduce new features"),
    Gitmoji("📝", ":memo:", "Add or update documentation"),
    Gitmoji("🚀", ":rocket:", "Deploy stuff"),
    Gitmoji("💄", ":lipstick:", "Add or update the UI and style files"),
    Gitmoji("🎉", ":tada:", "Begin a project"),
    Gitmoji("✅", ":white_check_mark:", "Add, update, or pass tests"),
    Gitmoji("🔒️", ":lock:", "Fix security or privacy issues"),
    Gitmoji("🔐", ":closed_lock_with_key:", "Add or update secrets"),
    Gitmoji("🔖", ":bookmark:", "Release / Version tags"),
    Gitmoji("🚨", ":rotating_light:", "Fix compiler / linter warnings"),
    Gitmoji("🚧", ":construction:", "Work in progress"),
    Gitmoji("💚", ":green_heart:", "Fix CI Build"),
    Gitmoji("⬇", ":arrow_down:", "Downgrade dependencies"),
    Gitmoji("⬆", ":arrow_up:", "Upgrade dependencies"),
    Gitmoji("📌", ":pushpin:", "Pin dependencies to specific versions"),
    Gitmoji("👷", ":construction_worker:", "Add or update CI build system"),
    Gitmoji("📈", ":chart_with_upwards_trend:", "Add or update analytics or track code"),
    Gitmoji("♻", ":recycle:", "Refactor code"),
    Gitmoji("➕", ":heavy_plus_sign:", "Add a dependency"),
    Gitmoji("➖", ":heavy_minus_sign:", "Remove a dependency"),
    Gitmoji("🔧", ":wrench:", "Add or update configuration files"),
    Gitmoji("🔨", ":hammer:", "Add or update development scripts"),
    Gitmoji("🌐", ":globe_with_meridians:", "Internationalization and localization"),
    Gitmoji("✏", ":pencil2:", "Fix typos"),
    Gitmoji("💩", ":poop:", "Write bad code that needs to be improved"),
    Gitmoji("⏪️", ":rewind:", "Revert changes"),
    Gitmoji("🔀", ":twisted_rightwards_arrows:", "Merge branches"),
    Gitmoji("📦️", ":package:", "Add or update compiled files or packages"),
    Gitmoji("👽️", ":alien:", "Update code due to external API changes"),
    Gitmoji("🚚", ":truck:", "Move or rename resources (e.g.: files, paths, routes)"),
    Gitmoji("📄", ":page_facing_up:", "Add or update license"),
    Gitmoji("💥", ":boom:", "Introduce breaking changes"),
    Gitmoji("🍱", ":bento:", "Add or update assets"),
    Gitmoji("♿️", ":wheelchair:", "Improve accessibility"),
    Gitmoji("💡", ":bulb:", "Add or update comments in source code"),
    Gitmoji("🍻", ":beers:", "Write code drunkenly"),
    Gitmoji("💬", ":speech_balloon:", "Add or update text and literals"),
    Gitmoji("🗃", ":card_file_box:", "Perform database related changes"),
    Gitmoji("🔊", ":loud_sound:", "Add or update logs"),
    Gitmoji("🔇", ":mute:", "Remove logs"),
    Gitmoji("👥", ":busts_in_silhouette:", "Add or update contributor(s)"),
    Gitmoji("🚸", ":children_crossing:", "Improve user experience / usability"),
    Gitmoji("🏗", ":building_construction:", "Make architectural changes"),
    Gitmoji("📱", ":iphone:", "Work on responsive design"),
    Gitmoji("🤡", ":clown_face:", "Mock things"),
    Gitmoji("🥚", ":egg:", "Add or update an easter egg"),
    Gitmoji("🙈", ":see_no_evil:", "Add or update a .gitignore file"),
    Gitmoji("📸", ":camera_flash:", "Add or update snapshots"),
    Gitmoji("⚗", ":alembic:", "Perform experiments"),
    Gitmoji("🔍️", ":mag:", "Improve SEO"),
    Gitmoji("🏷", ":label:", "Add or update types"),
    Gitmoji("🌱", ":seedling:", "Add or update seed files"),
    Gitmoji("🚩", ":triangular_flag_on_post:", "Add, update, or remove feature flags"),
    Gitmoji("🥅", ":goal_net:", "Catch errors"),
    Gitmoji("💫", ":dizzy:", "Add or update animations and transitions"),
    Gitmoji("🗑", ":wastebasket:", "Deprecate code that needs to be cleaned up"),
    Gitmoji(
        "🛂", ":passport_control:", "Work on code related to authorization, roles and permissions"
    ),
    Gitmoji("🩹", ":adhesive_bandage:", "Simple fix for a non-critical issue"),
    Gitmoji("🧐", ":monocle_face:", "Data exploration/inspection"),
    Gitmoji("⚰", ":coffin:", "Remove dead code"),
    Gitmoji("🧪", ":test_tube:", "Add a failing test"),
    Gitmoji("👔", ":necktie:", "Add or update business logic"),
    Gitmoji("🩺", ":stethoscope:", "Add or update healthcheck"),
    Gitmoji("🧱", ":bricks:", "Infrastructure related changes"),
    Gitmoji("🧑‍💻", ":technologist:", "Improve developer experience"),
    Gitmoji("💸", ":money_with_wings:", "Add sponsorships or money related infrastructure"),
    Gitmoji("🧵", ":thread:", "Add or update code related to multithreading or concurrency"),
    Gitmoji("🦺", ":safety_vest:", "Add or update code related to validation"),
]


def pick_gitmoji() -> Gitmoji | None:
    """Show an interactive menu to pick a gitmoji.

    Returns:
        The selected Gitmoji, or None if cancelled.
    """
    options = [
        Option({"value": gitmoji, "name": f"{gitmoji.emoji}  {gitmoji.description}"})
        for gitmoji in GITMOJIS
    ]

    result = Menu(
        label="Select a gitmoji for your commit:",
        options=options,
        allow_filtering=True,
        max_visible=10,
    ).ask()

    return result
