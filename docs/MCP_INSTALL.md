# MCP 클라이언트 연결 상세 가이드

GeryonMCP를 각 AI 에디터·클라이언트에 연결하는 방법입니다.
검색은 **로컬 전용(외부 호출 0)**이므로 연결 후 오프라인에서도 동작합니다.

> **전제**: `geryon bootstrap` + `geryon sync` 가 완료된 상태(색인 DB 존재).
> 아직 안 했다면 [설치 가이드](INSTALL_WALKTHROUGH.md)를 먼저 따르세요.

---

## 목차

1. [공통 설정 원칙](#1-공통-설정-원칙)
2. [Claude Code (CLI)](#2-claude-code-cli)
3. [Claude Desktop (macOS / Windows)](#3-claude-desktop-macos--windows)
4. [Cursor](#4-cursor)
5. [VS Code (GitHub Copilot Chat / Cline)](#5-vs-code)
6. [Zed](#6-zed)
7. [geryon watch — 자동 싱크 데몬](#7-geryon-watch--자동-싱크-데몬)
8. [문제 해결](#8-문제-해결)

---

## 1. 공통 설정 원칙

모든 클라이언트가 공유하는 핵심 JSON 블록입니다.

```json
{
  "mcpServers": {
    "geryon": {
      "command": "geryon",
      "args": ["serve"]
    }
  }
}
```

**격리 venv로 설치했다면** `command`에 절대경로를 씁니다:
```json
"command": "/절대경로/.venv/bin/geryon"
```

**팀 공유(프로젝트별 DB)라면** `env`로 DB 경로를 분리합니다:
```json
{
  "mcpServers": {
    "geryon": {
      "command": "geryon",
      "args": ["serve"],
      "env": { "GERYON_DB": "/path/to/myproj.db" }
    }
  }
}
```

**제공 도구** (`search` · `advanced_search` · `get_related` · `get_document` · `browse` · `list_sources` · `reindex`)

---

## 2. Claude Code (CLI)

### 2-a. 프로젝트 로컬 등록 (`.mcp.json`)

프로젝트 루트에 `.mcp.json` 을 만들거나 추가합니다:

```json
{
  "mcpServers": {
    "geryon": {
      "command": "geryon",
      "args": ["serve"]
    }
  }
}
```

저장 후 Claude Code를 재시작하거나 `/mcp` 명령으로 즉시 인식합니다.

```bash
# 등록 확인
claude mcp list
claude mcp get geryon
```

### 2-b. 전역 등록 (모든 프로젝트에서 항상 사용)

```bash
claude mcp add geryon -- geryon serve
```

특정 DB를 가리키려면:
```bash
claude mcp add geryon -e GERYON_DB=/path/to/myproj.db -- geryon serve
```

### 2-c. 동작 확인

Claude Code 세션에서:
```
"배포 프로세스 알려줘"      ← search 도구 자동 호출
/mcp                        ← 연결된 MCP 서버 목록 확인
```

---

## 3. Claude Desktop (macOS / Windows)

### 설정 파일 위치

| OS | 경로 |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

### 설정 내용

```json
{
  "mcpServers": {
    "geryon": {
      "command": "geryon",
      "args": ["serve"]
    }
  }
}
```

**격리 venv (macOS):**
```json
{
  "mcpServers": {
    "geryon": {
      "command": "/Users/<사용자명>/.venv/bin/geryon",
      "args": ["serve"]
    }
  }
}
```

**격리 venv (Windows):**
```json
{
  "mcpServers": {
    "geryon": {
      "command": "C:\\Users\\<사용자명>\\.venv\\Scripts\\geryon.exe",
      "args": ["serve"]
    }
  }
}
```

**프로젝트별 DB:**
```json
{
  "mcpServers": {
    "geryon": {
      "command": "geryon",
      "args": ["serve"],
      "env": { "GERYON_DB": "/Users/<사용자명>/.geryon/myproj.db" }
    }
  }
}
```

저장 후 **Claude Desktop 완전 종료 → 재시작**. 대화창에서 도구 버튼(망치 아이콘)에 `geryon` 도구가 나타나면 연결 완료.

---

## 4. Cursor

### 프로젝트 단위 등록 (팀 공유 — 권장)

프로젝트 루트 `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "geryon": {
      "command": "geryon",
      "args": ["serve"]
    }
  }
}
```

이 파일을 Git에 커밋하면 팀원이 clone 즉시 동일 MCP 설정을 씁니다.

### 전역 등록 (내 PC 모든 프로젝트)

`~/.cursor/mcp.json` (없으면 새로 만들기):

```json
{
  "mcpServers": {
    "geryon": {
      "command": "geryon",
      "args": ["serve"]
    }
  }
}
```

### 프로젝트별 DB 분리

```json
{
  "mcpServers": {
    "myproj-search": {
      "command": "geryon",
      "args": ["serve"],
      "env": { "GERYON_DB": "${workspaceFolder}/.geryon/myproj.db" }
    }
  }
}
```

> `${workspaceFolder}` 가 Cursor 버전에 따라 지원되지 않으면 절대경로로.

**적용**: Cursor 재시작 또는 `Ctrl+Shift+P → MCP: Reload Servers`.
**확인**: Agent 모드에서 `search` 도구가 보이면 성공.

---

## 5. VS Code

VS Code는 MCP를 지원하는 확장(`Cline`, `Copilot Chat`, `Continue` 등)을 통해 연결합니다.

### 5-a. `.vscode/mcp.json` (프로젝트 단위)

```json
{
  "servers": {
    "geryon": {
      "type": "stdio",
      "command": "geryon",
      "args": ["serve"]
    }
  }
}
```

> Claude Code 규격(`mcpServers`)과 키 이름이 다릅니다 — VS Code는 `servers` 아래 `type: "stdio"` 형식.

### 5-b. `settings.json` (전역)

```json
{
  "mcp.servers": {
    "geryon": {
      "type": "stdio",
      "command": "geryon",
      "args": ["serve"]
    }
  }
}
```

### 5-c. Cline 확장 사용 시

Cline 설정(`Extensions → Cline → MCP Servers`):
- **Name**: `geryon`
- **Command**: `geryon`
- **Args**: `serve`
- **Env**: `GERYON_DB=/path/to/db` (필요 시)

---

## 6. Zed

`~/.config/zed/settings.json`:

```json
{
  "context_servers": {
    "geryon": {
      "command": {
        "path": "geryon",
        "args": ["serve"]
      }
    }
  }
}
```

격리 venv이면 `"path"` 를 절대경로로:
```json
"path": "/home/<사용자명>/.venv/bin/geryon"
```

---

## 7. geryon watch — 자동 싱크 데몬

색인을 항상 최신으로 유지합니다. 기본값 **10분(600초)** 마다 `sync`를 자동 실행합니다.

### 기본 사용법

```bash
# 기본 10분 간격, Confluence 소스
geryon watch

# 소스·간격 지정
geryon watch --source confluence --interval 300   # 5분
geryon watch --source git --interval 1800         # 30분

# 전체 재구축(매회)
geryon watch --full

# 벡터 임베딩 생략(빠른 키워드 갱신)
geryon watch --no-vector

# 날짜 구간 고정 — 매회 해당 구간만 재수집
geryon watch --since 2026-01-01 --until 2026-06-30
```

출력 예:
```
[geryon watch] 자동 싱크 시작 — 소스: confluence, 간격: 10분 (Ctrl+C 로 종료)

[2026-06-25 10:00:00] 싱크 #1 시작 (소스: confluence)
  증분: DB 워터마크 2026-06-24 이후 수정분만 수집(--days/--all 로 변경)
  acquire: {...}
  ✓ 이미 최신 상태입니다 — 변경 없음(확인 0건).
  다음 싱크: 10:10:00 (약 10분 후) — Ctrl+C 로 중단
```

### 백그라운드 실행

```bash
# 백그라운드 실행(표준)
nohup geryon watch --interval 600 > ~/.geryon/watch.log 2>&1 &
echo $! > ~/.geryon/watch.pid

# 중지
kill $(cat ~/.geryon/watch.pid)
```

> 격리 venv·`uv tool` 설치라 `geryon` 이 PATH 에 없으면 절대경로로: `nohup /경로/.venv/bin/geryon watch …`.

### systemd 서비스 등록 (리눅스)

`~/.config/systemd/user/geryon-watch.service`:

```ini
[Unit]
Description=GeryonMCP 자동 싱크 데몬
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/geryon watch --source confluence --interval 600
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now geryon-watch
systemctl --user status geryon-watch
journalctl --user -u geryon-watch -f   # 로그 확인
```

격리 venv이면 `ExecStart`를 절대경로로:
```ini
ExecStart=/home/<사용자명>/.venv/bin/geryon watch --interval 600
```

### launchd 에이전트 등록 (macOS)

`~/Library/LaunchAgents/com.geryon.watch.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.geryon.watch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/geryon</string>
    <string>watch</string>
    <string>--source</string>
    <string>confluence</string>
    <string>--interval</string>
    <string>600</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/<사용자명>/.geryon/watch.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/<사용자명>/.geryon/watch.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.geryon.watch.plist
launchctl list | grep geryon   # 확인
launchctl unload ~/Library/LaunchAgents/com.geryon.watch.plist   # 중지
```

> 격리 venv이면 첫 `<string>/usr/local/bin/geryon</string>` 를 절대경로(`/Users/<사용자명>/.venv/bin/geryon`)로 바꾸세요 — launchd 는 셸 PATH 를 상속하지 않습니다.

### Windows 작업 스케줄러

PowerShell(관리자 권한):
```powershell
$action  = New-ScheduledTaskAction -Execute "geryon" -Argument "watch --source confluence --interval 600"
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "GeryonWatch" -Action $action -Trigger $trigger -RunLevel Highest
```

또는 GUI: **작업 스케줄러 → 새 작업 → 프로그램**: `geryon`, **인수**: `watch --interval 600`, **트리거**: 로그온 시.

> 격리 venv이면 `-Execute` 를 절대경로(`C:\Users\<사용자명>\.venv\Scripts\geryon.exe`)로 지정하세요.

### 여러 소스를 동시에 감시

`geryon watch` 를 `--source` 없이 실행하면 **한 프로세스에서 confluence+git+jira 를 모두** 같은 간격으로 감시합니다.
소스마다 **다른 간격**이 필요할 때만 아래처럼 별도 프로세스로 나눕니다:

```bash
# 각각 다른 간격으로 실행
geryon watch --source confluence --interval 600  &
geryon watch --source git        --interval 1800 &
geryon watch --source jira       --interval 900  &
```

---

## 8. 문제 해결

### 도구가 클라이언트에 안 나타나는 경우

```bash
# 서버 직접 실행해서 에러 확인
geryon serve

# 경로 확인
which geryon
# 출력이 없으면 PATH 문제 → 절대경로로 command 설정
```

### `geryon serve` 실행 시 에러

```bash
geryon health    # DB, 임베딩 모델, rerank 모델 점검
geryon status    # 색인 건수 확인
```

### DB가 비어 있거나 검색 결과 없음

```bash
geryon sync --source confluence --days 30   # 최근 30일 재수집·색인
geryon search "테스트 질의"                 # CLI로 직접 확인
```

### 클라이언트가 `geryon` 명령을 못 찾는 경우

GUI 클라이언트(Claude Desktop, Cursor)는 셸 PATH를 상속하지 않을 수 있습니다.

**해결 1** — `command`에 절대경로 사용:
```json
"command": "/usr/local/bin/geryon"
```

절대경로 확인:
```bash
which geryon
# macOS/Linux: /usr/local/bin/geryon 또는 ~/.local/bin/geryon 등
```

**해결 2** — 래퍼 스크립트 사용 (`~/.local/bin/geryon-mcp`):
```bash
#!/bin/bash
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
exec geryon serve
```
```bash
chmod +x ~/.local/bin/geryon-mcp
```
그 다음 `"command": "/Users/<사용자명>/.local/bin/geryon-mcp"`, `"args": []`.

### 연결은 되는데 검색이 느린 경우

- 최초 로드 시 모델 JIT(~5-10초) — 이후 캐시됨.
- 색인 건수가 많으면(수천 건+) 정상. `advanced_search` 대신 `search`를 먼저 시도.
- VRAM 없는 CPU 전용 환경에서는 rerank에 수 초 소요.

### `geryon watch` 종료 후 재시작이 안 되는 경우

```bash
# 잔여 프로세스 확인·정리
ps aux | grep "geryon watch"
kill <PID>
```

---

> 더 자세한 팀 배포 방법(방식 A/B, DB 공유, publish)은 [TEAM_DEPLOY.md](TEAM_DEPLOY.md)를 참고하세요.
> 폐쇄망(air-gap) 환경은 [AIRGAP_INSTALL.md](AIRGAP_INSTALL.md)를 참고하세요.
