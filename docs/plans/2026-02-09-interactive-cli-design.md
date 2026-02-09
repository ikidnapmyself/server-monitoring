# Interactive CLI for Server Maintenance

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a pure Bash interactive CLI in `bin/cli.sh` that guides users through project installation and all management commands with their flags and options.

**Architecture:** Single Bash script using `select` menus with hierarchical structure. Main menu leads to category submenus, each showing available commands with configurable options.

**Tech Stack:** Pure Bash (no dependencies), ANSI colors, select menus

---

## Example Output

```
╔══════════════════════════════════════════════════════════════╗
║              🔧 Server Maintenance CLI                       ║
╚══════════════════════════════════════════════════════════════╝

Select an option:
1) Install / Setup Project
2) Health & Monitoring
3) Alerts & Incidents
4) Intelligence & Recommendations
5) Pipeline Orchestration
6) Notifications
7) Exit

> 4

═══ Intelligence & Recommendations ═══

Select a command:
1) get_recommendations - Get AI recommendations
2) Back to main menu

> 1

═══ get_recommendations ═══

Available options:
  --memory     Analyze memory usage
  --disk       Analyze disk usage
  --all        Analyze everything
  --path=PATH  Path for disk analysis (default: /)
  --json       Output as JSON
  --provider=  Provider to use (default: local)

Select analysis type:
1) Memory analysis (--memory)
2) Disk analysis (--disk)
3) Full analysis (--all)
4) Custom options
5) Back

> 2

Enter path to analyze [/var/log]: /tmp

Command to run:
  uv run python manage.py get_recommendations --disk --path=/tmp

Run this command? (y/n): y

⠋ Running command...
[command output here]
```

---

## Task 1: Create base script with banner and main menu

**Files:**
- Create: `bin/cli.sh`

**Step 1: Write the base script**

```bash
#!/usr/bin/env bash
#
# Interactive CLI for Server Maintenance
# Usage: ./bin/cli.sh
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Change to project root
cd "$PROJECT_ROOT"

show_banner() {
    clear
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║              🔧 Server Maintenance CLI                       ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

show_main_menu() {
    echo -e "${BOLD}Select an option:${NC}"
    echo ""

    local options=(
        "Install / Setup Project"
        "Health & Monitoring"
        "Alerts & Incidents"
        "Intelligence & Recommendations"
        "Pipeline Orchestration"
        "Notifications"
        "Exit"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1) install_project ;;
            2) health_menu ;;
            3) alerts_menu ;;
            4) intelligence_menu ;;
            5) pipeline_menu ;;
            6) notify_menu ;;
            7) echo -e "${GREEN}Goodbye!${NC}"; exit 0 ;;
            *) echo -e "${RED}Invalid option${NC}" ;;
        esac
        break
    done
}

# Placeholder functions (will be implemented in subsequent tasks)
install_project() { echo "Not implemented yet"; }
health_menu() { echo "Not implemented yet"; }
alerts_menu() { echo "Not implemented yet"; }
intelligence_menu() { echo "Not implemented yet"; }
pipeline_menu() { echo "Not implemented yet"; }
notify_menu() { echo "Not implemented yet"; }

# Main loop
main() {
    while true; do
        show_banner
        show_main_menu
        echo ""
        read -p "Press Enter to continue..."
    done
}

main "$@"
```

**Step 2: Make executable and test**

```bash
chmod +x bin/cli.sh
./bin/cli.sh
```

**Step 3: Commit**

```bash
git add bin/cli.sh
git commit -m "feat(cli): add interactive CLI base with main menu"
```

---

## Task 2: Add install/setup function

**Files:**
- Modify: `bin/cli.sh`

**Step 1: Implement install_project function**

```bash
install_project() {
    show_banner
    echo -e "${BOLD}═══ Install / Setup Project ═══${NC}"
    echo ""

    local options=(
        "Full installation (uv sync + pre-commit)"
        "Install dependencies only (uv sync)"
        "Install pre-commit hooks"
        "Check installation status"
        "Back to main menu"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                echo -e "${YELLOW}Running full installation...${NC}"
                run_command "uv sync" "Installing dependencies"
                run_command "uv run pre-commit install" "Installing pre-commit hooks"
                ;;
            2)
                run_command "uv sync" "Installing dependencies"
                ;;
            3)
                run_command "uv run pre-commit install" "Installing pre-commit hooks"
                ;;
            4)
                check_installation
                ;;
            5)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

check_installation() {
    echo -e "${BOLD}Checking installation status...${NC}"
    echo ""

    # Check uv
    if command -v uv &> /dev/null; then
        echo -e "${GREEN}✓${NC} uv is installed ($(uv --version))"
    else
        echo -e "${RED}✗${NC} uv is not installed"
    fi

    # Check .venv
    if [ -d ".venv" ]; then
        echo -e "${GREEN}✓${NC} Virtual environment exists"
    else
        echo -e "${RED}✗${NC} Virtual environment not found"
    fi

    # Check pre-commit
    if [ -f ".git/hooks/pre-commit" ]; then
        echo -e "${GREEN}✓${NC} Pre-commit hooks installed"
    else
        echo -e "${YELLOW}!${NC} Pre-commit hooks not installed"
    fi

    # Check Django
    if uv run python manage.py check &> /dev/null; then
        echo -e "${GREEN}✓${NC} Django is configured correctly"
    else
        echo -e "${RED}✗${NC} Django check failed"
    fi
}

run_command() {
    local cmd="$1"
    local description="${2:-Running command}"

    echo ""
    echo -e "${CYAN}Command: ${cmd}${NC}"
    echo ""

    if eval "$cmd"; then
        echo ""
        echo -e "${GREEN}✓ ${description} completed successfully${NC}"
    else
        echo ""
        echo -e "${RED}✗ ${description} failed${NC}"
    fi
}
```

**Step 2: Test**

```bash
./bin/cli.sh
# Select option 1, then option 4 to check status
```

**Step 3: Commit**

```bash
git add bin/cli.sh
git commit -m "feat(cli): add install/setup menu with status check"
```

---

## Task 3: Add health monitoring menu

**Files:**
- Modify: `bin/cli.sh`

**Step 1: Implement health_menu function**

```bash
health_menu() {
    show_banner
    echo -e "${BOLD}═══ Health & Monitoring ═══${NC}"
    echo ""
    echo -e "${CYAN}Command: check_health${NC}"
    echo "Check system health metrics (CPU, memory, disk)"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  --environment=ENV  Environment name (default: development)"
    echo "  --json             Output as JSON"
    echo ""

    local options=(
        "Run health check (default)"
        "Run with JSON output"
        "Specify environment"
        "Back to main menu"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py check_health"
                ;;
            2)
                confirm_and_run "uv run python manage.py check_health --json"
                ;;
            3)
                read -p "Enter environment name [development]: " env_name
                env_name="${env_name:-development}"
                confirm_and_run "uv run python manage.py check_health --environment=$env_name"
                ;;
            4)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

confirm_and_run() {
    local cmd="$1"
    echo ""
    echo -e "${BOLD}Command to run:${NC}"
    echo -e "  ${CYAN}${cmd}${NC}"
    echo ""
    read -p "Run this command? (y/n): " confirm

    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        echo ""
        eval "$cmd"
    else
        echo -e "${YELLOW}Command cancelled${NC}"
    fi
}
```

**Step 2: Test**

```bash
./bin/cli.sh
# Select option 2 (Health & Monitoring)
```

**Step 3: Commit**

```bash
git add bin/cli.sh
git commit -m "feat(cli): add health monitoring menu"
```

---

## Task 4: Add alerts menu

**Files:**
- Modify: `bin/cli.sh`

**Step 1: Implement alerts_menu function**

```bash
alerts_menu() {
    show_banner
    echo -e "${BOLD}═══ Alerts & Incidents ═══${NC}"
    echo ""

    local options=(
        "run_check - Run a specific checker"
        "check_and_alert - Run checker and create alert"
        "Back to main menu"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1) run_check_menu ;;
            2) check_and_alert_menu ;;
            3) return ;;
            *) echo -e "${RED}Invalid option${NC}" ;;
        esac
        break
    done
}

run_check_menu() {
    show_banner
    echo -e "${BOLD}═══ run_check ═══${NC}"
    echo ""
    echo "Run a health checker and display results"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  CHECKER_NAME       Name of checker to run (required)"
    echo "  --list             List available checkers"
    echo "  --json             Output as JSON"
    echo ""

    local options=(
        "List available checkers"
        "Run a checker"
        "Run with JSON output"
        "Back"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py run_check --list"
                ;;
            2)
                read -p "Enter checker name: " checker_name
                if [ -n "$checker_name" ]; then
                    confirm_and_run "uv run python manage.py run_check $checker_name"
                else
                    echo -e "${RED}Checker name required${NC}"
                fi
                ;;
            3)
                read -p "Enter checker name: " checker_name
                if [ -n "$checker_name" ]; then
                    confirm_and_run "uv run python manage.py run_check $checker_name --json"
                else
                    echo -e "${RED}Checker name required${NC}"
                fi
                ;;
            4)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

check_and_alert_menu() {
    show_banner
    echo -e "${BOLD}═══ check_and_alert ═══${NC}"
    echo ""
    echo "Run checker and create incident if threshold exceeded"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  CHECKER_NAME           Name of checker (required)"
    echo "  --threshold=VALUE      Alert threshold (required)"
    echo "  --severity=LEVEL       Severity: info|warning|error|critical"
    echo "  --title=TITLE          Custom incident title"
    echo ""

    read -p "Enter checker name: " checker_name
    if [ -z "$checker_name" ]; then
        echo -e "${RED}Checker name required${NC}"
        return
    fi

    read -p "Enter threshold value: " threshold
    if [ -z "$threshold" ]; then
        echo -e "${RED}Threshold required${NC}"
        return
    fi

    read -p "Enter severity [warning]: " severity
    severity="${severity:-warning}"

    read -p "Enter custom title (optional): " title

    local cmd="uv run python manage.py check_and_alert $checker_name --threshold=$threshold --severity=$severity"
    if [ -n "$title" ]; then
        cmd="$cmd --title=\"$title\""
    fi

    confirm_and_run "$cmd"
}
```

**Step 2: Test**

```bash
./bin/cli.sh
# Select option 3 (Alerts & Incidents)
```

**Step 3: Commit**

```bash
git add bin/cli.sh
git commit -m "feat(cli): add alerts menu with run_check and check_and_alert"
```

---

## Task 5: Add intelligence menu

**Files:**
- Modify: `bin/cli.sh`

**Step 1: Implement intelligence_menu function**

```bash
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
```

**Step 2: Test**

```bash
./bin/cli.sh
# Select option 4 (Intelligence & Recommendations)
```

**Step 3: Commit**

```bash
git add bin/cli.sh
git commit -m "feat(cli): add intelligence menu with get_recommendations"
```

---

## Task 6: Add pipeline menu

**Files:**
- Modify: `bin/cli.sh`

**Step 1: Implement pipeline_menu function**

```bash
pipeline_menu() {
    show_banner
    echo -e "${BOLD}═══ Pipeline Orchestration ═══${NC}"
    echo ""

    local options=(
        "run_pipeline - Execute a pipeline"
        "monitor_pipeline - Monitor pipeline execution"
        "Back to main menu"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1) run_pipeline_menu ;;
            2) monitor_pipeline_menu ;;
            3) return ;;
            *) echo -e "${RED}Invalid option${NC}" ;;
        esac
        break
    done
}

run_pipeline_menu() {
    show_banner
    echo -e "${BOLD}═══ run_pipeline ═══${NC}"
    echo ""
    echo "Execute a pipeline definition"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  PIPELINE_NAME      Name of pipeline (or path to definition)"
    echo "  --list             List available pipelines"
    echo "  --dry-run          Show what would be executed"
    echo "  --json             Output as JSON"
    echo ""

    local options=(
        "List available pipelines"
        "Run a pipeline"
        "Dry run (preview)"
        "Back"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py run_pipeline --list"
                ;;
            2)
                read -p "Enter pipeline name: " pipeline_name
                if [ -n "$pipeline_name" ]; then
                    confirm_and_run "uv run python manage.py run_pipeline $pipeline_name"
                else
                    echo -e "${RED}Pipeline name required${NC}"
                fi
                ;;
            3)
                read -p "Enter pipeline name: " pipeline_name
                if [ -n "$pipeline_name" ]; then
                    confirm_and_run "uv run python manage.py run_pipeline $pipeline_name --dry-run"
                else
                    echo -e "${RED}Pipeline name required${NC}"
                fi
                ;;
            4)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

monitor_pipeline_menu() {
    show_banner
    echo -e "${BOLD}═══ monitor_pipeline ═══${NC}"
    echo ""
    echo "Monitor pipeline execution status"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  PIPELINE_ID        ID of pipeline execution to monitor"
    echo "  --list             List recent pipeline executions"
    echo "  --follow           Follow execution in real-time"
    echo ""

    local options=(
        "List recent executions"
        "Monitor a pipeline"
        "Follow pipeline (real-time)"
        "Back"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py monitor_pipeline --list"
                ;;
            2)
                read -p "Enter pipeline execution ID: " pipeline_id
                if [ -n "$pipeline_id" ]; then
                    confirm_and_run "uv run python manage.py monitor_pipeline $pipeline_id"
                else
                    echo -e "${RED}Pipeline ID required${NC}"
                fi
                ;;
            3)
                read -p "Enter pipeline execution ID: " pipeline_id
                if [ -n "$pipeline_id" ]; then
                    confirm_and_run "uv run python manage.py monitor_pipeline $pipeline_id --follow"
                else
                    echo -e "${RED}Pipeline ID required${NC}"
                fi
                ;;
            4)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}
```

**Step 2: Test**

```bash
./bin/cli.sh
# Select option 5 (Pipeline Orchestration)
```

**Step 3: Commit**

```bash
git add bin/cli.sh
git commit -m "feat(cli): add pipeline menu with run_pipeline and monitor_pipeline"
```

---

## Task 7: Add notifications menu

**Files:**
- Modify: `bin/cli.sh`

**Step 1: Implement notify_menu function**

```bash
notify_menu() {
    show_banner
    echo -e "${BOLD}═══ Notifications ═══${NC}"
    echo ""

    local options=(
        "list_notify_drivers - List available notification drivers"
        "test_notify - Send a test notification"
        "Back to main menu"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py list_notify_drivers"
                ;;
            2)
                test_notify_menu
                ;;
            3)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

test_notify_menu() {
    show_banner
    echo -e "${BOLD}═══ test_notify ═══${NC}"
    echo ""
    echo "Send a test notification to verify driver configuration"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  DRIVER_NAME        Name of notification driver (required)"
    echo "  --channel=NAME     Specific channel/recipient"
    echo "  --message=TEXT     Custom test message"
    echo ""

    read -p "Enter driver name: " driver_name
    if [ -z "$driver_name" ]; then
        echo -e "${RED}Driver name required${NC}"
        return
    fi

    read -p "Enter channel (optional): " channel
    read -p "Enter custom message (optional): " message

    local cmd="uv run python manage.py test_notify $driver_name"
    if [ -n "$channel" ]; then
        cmd="$cmd --channel=$channel"
    fi
    if [ -n "$message" ]; then
        cmd="$cmd --message=\"$message\""
    fi

    confirm_and_run "$cmd"
}
```

**Step 2: Test**

```bash
./bin/cli.sh
# Select option 6 (Notifications)
```

**Step 3: Commit**

```bash
git add bin/cli.sh
git commit -m "feat(cli): add notifications menu with list_notify_drivers and test_notify"
```

---

## Task 8: Final polish and testing

**Files:**
- Modify: `bin/cli.sh`

**Step 1: Add help option and improve UX**

```bash
# Add at top after colors
show_help() {
    echo "Usage: ./bin/cli.sh [command]"
    echo ""
    echo "Interactive CLI for Server Maintenance"
    echo ""
    echo "Commands:"
    echo "  (no args)    Start interactive mode"
    echo "  help         Show this help message"
    echo "  install      Jump to installation menu"
    echo "  health       Jump to health monitoring"
    echo "  alerts       Jump to alerts menu"
    echo "  intel        Jump to intelligence menu"
    echo "  pipeline     Jump to pipeline menu"
    echo "  notify       Jump to notifications menu"
    echo ""
}

# Update main() to handle arguments
main() {
    case "${1:-}" in
        help|--help|-h)
            show_help
            exit 0
            ;;
        install)
            show_banner
            install_project
            ;;
        health)
            show_banner
            health_menu
            ;;
        alerts)
            show_banner
            alerts_menu
            ;;
        intel|intelligence)
            show_banner
            intelligence_menu
            ;;
        pipeline)
            show_banner
            pipeline_menu
            ;;
        notify)
            show_banner
            notify_menu
            ;;
        "")
            while true; do
                show_banner
                show_main_menu
                echo ""
                read -p "Press Enter to continue..."
            done
            ;;
        *)
            echo "Unknown command: $1"
            show_help
            exit 1
            ;;
    esac
}
```

**Step 2: Test all menus**

```bash
# Test interactive mode
./bin/cli.sh

# Test direct commands
./bin/cli.sh help
./bin/cli.sh health
./bin/cli.sh intel
```

**Step 3: Commit**

```bash
git add bin/cli.sh
git commit -m "feat(cli): add help and direct command shortcuts"
```

---

## Summary

| Task | Description |
|------|-------------|
| 1 | Create base script with banner and main menu |
| 2 | Add install/setup function |
| 3 | Add health monitoring menu |
| 4 | Add alerts menu |
| 5 | Add intelligence menu |
| 6 | Add pipeline menu |
| 7 | Add notifications menu |
| 8 | Final polish and testing |
