"""Save editor for the game Dressmaker."""

__version__ = "1.0.1"  # x-release-please-version


def full_version() -> str:
    """The version including build metadata, e.g. ``1.0.0+dev.abc1234``.

    Release builds and source checkouts report the plain version. Development
    builds get ``+dev.<commit>`` (semver build metadata) from a ``build.txt``
    that the build script bundles, so they never pass for a release.
    """
    from importlib import resources

    try:
        build = resources.files("dressmaker_editor.data").joinpath("build.txt").read_text().strip()
    except OSError, ModuleNotFoundError:
        build = ""
    return f"{__version__}+{build}" if build else __version__
