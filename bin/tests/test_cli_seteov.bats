#!/usr/bin/env bats
#
# Regression test for the `set -e` submenu-abort bug.
#
# bin/cli.sh runs under `set -e`. In the converted tuin submenus, each menu
# action is dispatched via a `case ... esac` whose last command is often
# `confirm_and_run "..."`. `confirm_and_run` returns non-zero when the user
# declines the confirm OR when the eval'd command exits non-zero. Because that
# was the last command in the case arm, the whole `case` yielded non-zero, and
# under `set -e` that aborted the submenu function — killing the entire CLI
# instead of looping back to the menu.
#
# The fix appends `|| true` to the action-dispatch `esac`, suppressing `set -e`
# for the dynamic extent of the case so the loop survives and keeps looping
# until Back. This test proves health_menu survives a declined/failed confirm
# under `set -e`.

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "health_menu survives a declined confirm under set -e" {
    run bash -c '
        set -e
        source "'"$BIN_DIR"'/lib/colors.sh"
        source "'"$BIN_DIR"'/lib/tuin.sh"
        show_banner() { :; }
        run_command() { :; }
        confirm_and_run() { return 1; }   # simulate decline / failed command
        source "'"$BIN_DIR"'/cli/health.sh"
        # Non-interactive tuin_menu reads a 1-indexed line; health has 8 options
        # so Back is index 9. The pause (tuin_input) consumes one line.
        # pick 1 (Run all health checks) -> confirm_and_run returns 1
        # ""       consumed by the "Press Enter to continue" pause
        # 9        Back -> tuin_menu returns non-zero -> health_menu returns 0
        printf "1\n\n9\n" | health_menu
        echo "SURVIVED rc=$?"
    '
    assert_success
    assert_output --partial "SURVIVED"
}