# shellcheck shell=bash
# Sourced by cli.sh — do not execute directly.

intelligence_menu() {
    local default_path="$PROJECT_DIR"
    while true; do
        show_banner
        tuin_section "Intelligence & Recommendations"
        echo "AI-powered recommendations for system optimization."
        echo ""
        if tuin_menu "Intelligence" \
            "Memory analysis" \
            "Disk analysis" \
            "Full analysis (memory + disk)" \
            "Custom options" \
            "List providers"
        then
            case $TUIN_REPLY in
                "Memory analysis")
                    confirm_and_run "uv run python manage.py get_recommendations --memory" ;;
                "Disk analysis")
                    disk_path=$(tuin_input "Enter path to analyze" "$default_path")
                    confirm_and_run "uv run python manage.py get_recommendations --disk --path=$disk_path" ;;
                "Full analysis (memory + disk)")
                    disk_path=$(tuin_input "Enter path for disk analysis" "$default_path")
                    confirm_and_run "uv run python manage.py get_recommendations --all --path=$disk_path" ;;
                "Custom options")
                    custom_recommendations ;;
                "List providers")
                    confirm_and_run "uv run python manage.py get_recommendations --list-providers" ;;
            esac || true
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            return 0
        fi
    done
}

custom_recommendations() {
    tuin_section "Configure custom analysis"

    local cmd="uv run python manage.py get_recommendations"

    if tuin_confirm "Include memory analysis?" y; then
        cmd="$cmd --memory"
    fi

    if tuin_confirm "Include disk analysis?" y; then
        cmd="$cmd --disk"
        disk_path=$(tuin_input "Path to analyze" "$PROJECT_DIR")
        cmd="$cmd --path=$disk_path"
    fi

    top_n=$(tuin_input "Top N processes" "10")
    if [ -n "$top_n" ]; then
        cmd="$cmd --top-n=$top_n"
    fi

    threshold_mb=$(tuin_input "Large file threshold MB" "100")
    if [ -n "$threshold_mb" ]; then
        cmd="$cmd --threshold-mb=$threshold_mb"
    fi

    if tuin_confirm "Output as JSON?" n; then
        cmd="$cmd --json"
    fi

    confirm_and_run "$cmd"
}