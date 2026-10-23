"""The system integration for this computer: ``shell`` on Windows (registry, Start Menu, Explorer's
right-click menu), ``mac.integration`` on macOS (Finder Quick Action, LaunchAgent). Both offer the same functions."""

from .system import IS_MAC

if IS_MAC:
    from .mac.integration import (context_menu_registered, install, register_context_menu,  # noqa: F401
                                  set_startup, startup_enabled, uninstall, uninstall_everything,
                                  unregister_context_menu)
else:
    from .shell import (context_menu_registered, install, register_context_menu,  # noqa: F401
                        set_startup, startup_enabled, uninstall, uninstall_everything,
                        unregister_context_menu)
