# Sourced by cli.sh — do not execute directly.

intelligence_menu() {
    show_banner
    echo -e "${BOLD}═══ Intelligence & Recommendations ═══${NC}"
    echo ""
    echo -e "${CYAN}Command: get_recommendations${NC}"
    echo "Get AI-powered recommendations for system optimization"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  --memory           Analyze memory usage"
    echo "  --disk             Analyze disk usage"
    echo "  --all              Analyze everything"
    echo "  --path=PATH        Path for disk analysis (default: /)"
    echo "  --top-n=N          Number of top processes (default: 10)"
    echo "  --threshold-mb=MB  Large file threshold (default: 100)"
    echo "  --old-days=DAYS    Old file age in days (default: 30)"
    echo "  --json             Output as JSON"
    echo "  --provider=NAME    Provider to use (default: local)"
    echo "  --list-providers   List available providers"
    echo ""

    local options=(
        "Memory analysis"
        "Disk analysis"
        "Full analysis (memory + disk)"
        "Custom options"
        "List providers"
        "Back to main menu"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py get_recommendations --memory"
                ;;
            2)
                read -p "Enter path to analyze [/var/log]: " disk_path
                disk_path="${disk_path:-/var/log}"
                confirm_and_run "uv run python manage.py get_recommendations --disk --path=$disk_path"
                ;;
            3)
                confirm_and_run "uv run python manage.py get_recommendations --all"
                ;;
            4)
                custom_recommendations
                ;;
            5)
                confirm_and_run "uv run python manage.py get_recommendations --list-providers"
                ;;
            6)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

custom_recommendations() {
    echo ""
    echo -e "${BOLD}Configure custom analysis:${NC}"

    local cmd="uv run python manage.py get_recommendations"

    read -p "Include memory analysis? (y/n) [y]: " inc_memory
    if [[ "${inc_memory:-y}" =~ ^[Yy]$ ]]; then
        cmd="$cmd --memory"
    fi

    read -p "Include disk analysis? (y/n) [y]: " inc_disk
    if [[ "${inc_disk:-y}" =~ ^[Yy]$ ]]; then
        cmd="$cmd --disk"
        read -p "  Path to analyze [/]: " disk_path
        if [ -n "$disk_path" ]; then
            cmd="$cmd --path=$disk_path"
        fi
    fi

    read -p "Top N processes [10]: " top_n
    if [ -n "$top_n" ]; then
        cmd="$cmd --top-n=$top_n"
    fi

    read -p "Large file threshold MB [100]: " threshold_mb
    if [ -n "$threshold_mb" ]; then
        cmd="$cmd --threshold-mb=$threshold_mb"
    fi

    read -p "Output as JSON? (y/n) [n]: " use_json
    if [[ "$use_json" =~ ^[Yy]$ ]]; then
        cmd="$cmd --json"
    fi

    confirm_and_run "$cmd"
}