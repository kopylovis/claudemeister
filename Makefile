PREFIX ?= $(HOME)/.local
BIN := $(PREFIX)/bin

.PHONY: install uninstall test

install:
	@mkdir -p "$(BIN)"
	@ln -sfn "$(CURDIR)/bin/claudemeister" "$(BIN)/claudemeister"
	@echo "claudemeister -> $(BIN)/claudemeister (symlink на проект, правки работают сразу)"

uninstall:
	@rm -f "$(BIN)/claudemeister"
	@echo "claudemeister удалён из $(BIN)"

test:
	@bash -n bin/claudemeister
	@/usr/bin/python3 -m unittest discover -s tests -q 2>&1 | tail -1 | grep -qx OK || /usr/bin/python3 -m unittest discover -s tests
	@bash -n libexec/claudemeister/claude.sh
	@for f in libexec/claudemeister/*.py libexec/claudemeister/lib/*.py; do /usr/bin/python3 -m py_compile "$$f" || exit 1; done
	@./bin/claudemeister --version | grep -qx "$$(cat VERSION)"
	@./bin/claudemeister help | grep -q 'claudemeister setup'
	@printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | ./bin/claudemeister mcp | grep -q open_claude
	@./bin/claudemeister deps status -h >/dev/null
	@swiftc -typecheck -swift-version 5 share/claudemeister/notify/main.swift
	@./bin/claudemeister notify -h | grep -q telegram
	@./bin/claudemeister deps -h >/dev/null
	@./bin/claudemeister notify -h | grep -q "notify phone"
	@./bin/claudemeister proxy -h | grep -q mihomo
	@./bin/claudemeister -h | grep -q -- '-x  '
	@XDG_CONFIG_HOME=/nonexistent-cm ./bin/claudemeister proxy run -- true 2>/dev/null; test $$? -eq 2
	@XDG_CONFIG_HOME=/nonexistent-cm ./bin/claudemeister proxy >/dev/null; test $$? -eq 1
	@! XDG_CONFIG_HOME=/nonexistent-cm ./bin/claudemeister proxy configured
	@./bin/claudemeister proxy setup not-a-url </dev/null >/dev/null; test $$? -ne 0
	@./bin/claudemeister setup --country KZ --bogus 2>&1 >/dev/null | grep -q 'не знаю --bogus'
	@./bin/claudemeister setup --country KAZ 2>/dev/null; test $$? -eq 2
	@./bin/claudemeister setup -h | grep -q -- '--country'
	@./bin/claudemeister doctor -h | grep -q 'проверить установку'
	@HOME=/nonexistent XDG_CONFIG_HOME=/nonexistent-cm /usr/bin/python3 libexec/claudemeister/lib/claude_setup.py pending | grep -q '^fresh commands .*proxy promptsill$$'
	@HOME=/nonexistent XDG_CONFIG_HOME=/nonexistent-cm /usr/bin/python3 libexec/claudemeister/lib/claude_setup.py check </dev/null | wc -c | grep -q '^ *0$$'
	@./bin/claudemeister guard >/dev/null
	@./bin/claudemeister guard -h >/dev/null
	@./bin/claudemeister humanize -h | grep -q ПРГЕН
	@./bin/claudemeister humanize rules | grep -q 'Проверка перед отправкой'
	@printf 'Конечно! Это не только быстро, но и удобно.\n' | ./bin/claudemeister humanize check - | grep -q 'не только'
	@printf 'Сборка шла 4 минуты, теперь 50 секунд.\n' | ./bin/claudemeister humanize check - >/dev/null
	@echo junk | ./bin/claudemeister humanize-hook | wc -c | grep -q '^ *0$$'
	@printf '%s\n' '---' '- `X` — пояснение' | ./bin/claudemeister humanize check - | grep 'разделитель' | grep -q '\-\-\-'
	@printf '%s\n' '- `X` — пояснение' | ./bin/claudemeister humanize check - | grep -q 'термин — пояснение'
	@printf '%s\n' 'Еще «тест» ёж. Итог: да. Причина: нет.' | ./bin/claudemeister humanize check - | grep 'елочки' | grep -q 'шт'
	@printf '%s\n' 'Еще «тест» ёж. Итог: да. Причина: нет.' | ./bin/claudemeister humanize check - | grep 'точками' | grep -q 'шт'
	@printf '%s\n' 'Итог: да. Причина: нет.' | ./bin/claudemeister humanize check - | grep -q 'двоеточия'
	@printf '%s\n' 'В современном мире это потенциально может помочь. Буду честен, время покажет. citeturn0search0' | ./bin/claudemeister humanize check - | grep -c 'вступление-штамп\|модальная вата\|показная честность\|пустой прогноз\|след чат' | grep -q 5
	@./bin/claudemeister humanize rules | grep -q '## 7. Проверка перед отправкой'
	@cd /tmp && $(CURDIR)/bin/claudemeister guard test 'git push -f origin master' | grep -q destructive
	@$(CURDIR)/bin/claudemeister guard test 'git push origin master' | grep -q пропущу
	@$(CURDIR)/bin/claudemeister guard test 'rm -rf build' | grep -q пропущу
	@$(CURDIR)/bin/claudemeister guard test 'rm -rf ~/Documents/old' | grep -q destructive
	@$(CURDIR)/bin/claudemeister guard test 'bundle exec fastlane beta' | grep -q пропущу
	@$(CURDIR)/bin/claudemeister guard test 'cat fastlane/.env.default' | grep -q secrets-read
	@$(CURDIR)/bin/claudemeister guard test 'rm -rf ~/.ssh' | grep -q secrets-delete
	@$(CURDIR)/bin/claudemeister guard test 'security delete-generic-password -s x' | grep -q secrets-delete
	@echo '{"tool_name":"Bash","tool_input":{"command":"ls"}}' | ./bin/claudemeister guard-hook | wc -c | grep -q '^ *0$$'
	@echo '{"tool_name":"Read","permission_mode":"auto","session_id":"x","tool_input":{"file_path":"/x/.env"}}' | XDG_CONFIG_HOME=/nonexistent-cm ./bin/claudemeister guard-hook | grep -q '"deny"'
	@echo '{}' | ./bin/claudemeister statusline >/dev/null
	@echo '{}' | MNRH_PROMPTSILL=/nonexistent ./bin/claudemeister statusline | grep -q promptsill
	@MNRH_PROMPTSILL=/nonexistent ./bin/claudemeister statusline -h | grep -q 'brew install kopylovis/tap/promptsill'
	@printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | ./bin/claudemeister mcp | grep -q '"deps_outdated"'
	@./bin/claudemeister notify >/dev/null
	@echo junk | ./bin/claudemeister notify-hook | wc -c | grep -q '^ *0$$'
	@echo '{"session_id":"00000000-0000-0000-0000-000000000000","hook_event_name":"UserPromptSubmit","user_input":"x"}' | ./bin/claudemeister notify-hook | wc -c | grep -q '^ *0$$'
	@./bin/claudemeister -h >/dev/null
	@./bin/claudemeister sessions -h >/dev/null
	@./bin/claudemeister sessions -l >/dev/null
	@./bin/claudemeister sessions search mnrh-no-such-word-zq >/dev/null
	@./bin/claudemeister sessions choose /nonexistent-cm | grep -qx new
	@./bin/claudemeister sessions -h | grep -q slim
	@./bin/claudemeister slim -h >/dev/null
	@./bin/claudemeister todo -h >/dev/null
	@./bin/claudemeister shot -h >/dev/null
	@./bin/claudemeister brief -h >/dev/null
	@./bin/claudemeister parallel -h >/dev/null
	@./bin/claudemeister ask -h >/dev/null
	@./bin/claudemeister release-notes -h >/dev/null
	@cd /tmp && $(CURDIR)/bin/claudemeister brief >/dev/null
	@printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | ./bin/claudemeister mcp | grep '"todo_add"' | grep '"parallel_task"' | grep '"window_screenshot"' | grep '"ask_project"' | grep -q '"release_notes_write"'
	@printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | ./bin/claudemeister mcp | grep -q '"slim"'
	@! ./bin/claudemeister sessions slim no-such-session >/dev/null 2>&1
	@! ./bin/claudemeister sessions show no-such-session >/dev/null
	@if command -v mnrh >/dev/null; then printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | ./bin/claudemeister mcp | grep '"session_search"' | grep '"session_read"' | grep '"mac_status"' | grep -q '"free_memory"'; fi
	@printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | ./bin/claudemeister mcp | grep -q "\"version\": \"$$(cat VERSION)\""
	@if command -v mnrh >/dev/null; then printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"mac_status","arguments":{"parts":["ports"]}}}' | ./bin/claudemeister mcp | grep -q '"isError": false'; fi
	@./bin/claudemeister restart -h >/dev/null
	@./bin/claudemeister forget -h >/dev/null
	@echo junk | ./bin/claudemeister notice | wc -c | grep -q '^ *0$$'
	@echo junk | ./bin/claudemeister session-end | wc -c | grep -q '^ *0$$'
	@echo '{"session_id":"00000000-0000-0000-0000-000000000000"}' | ./bin/claudemeister notice | wc -c | grep -q '^ *0$$'
	@zsh -n share/claudemeister/restart.zsh
	@printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | ./bin/claudemeister mcp | grep '"restart"' | grep -q '"forget"'
	@test -f share/claudemeister/icon.png
	@./bin/claudemeister </dev/null >/dev/null 2>&1; test $$? -eq 2
	@./bin/claudemeister open </dev/null >/dev/null 2>&1; test $$? -eq 2
	@! printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | PATH=/usr/bin:/bin ./bin/claudemeister mcp | grep -q '"mac_status"'
	@printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | ./bin/claudemeister mcp | grep -q '"name": "claudemeister"'
	@find libexec -name __pycache__ -type d -exec rm -rf {} +
	@echo "все проверки пройдены"
