# DSH CLI status — 2026-10-17

## Outcome

The requested isolated installation was attempted at `C:\Users\Yuri368\.codex\tools\deepseek-harness-cli` for the official package `@deepseek-ai/dsh@0.2.0-rc.2`. The target path did not exist before the attempt. `npm install --prefix ... --no-save --no-audit --no-fund` remained active without producing output or files; it was stopped as a bounded attempt after the main task reported no usable key. The target directory exists but contains zero files. No package was installed, no DSH command was run, and no session was started.

## Credential and safety findings

- The current process has no `DEEPSEEK_API_KEY` environment variable and no `DSH_HOME` override. No credential values, credential files, or desktop profile data were read.
- Official headless documentation says the default `headless` profile disables the managed credential store and its authorization/DeepSeek-account consumers. It ignores keys and sign-ins saved by the GUI. API-key auth can come from process environment, invocation cwd `.env`, or `$DSH_HOME/.env`; none was inspected or loaded by this attempt.
- Official docs say headless is one-shot and has no GUI, server, or browser, but uses the same model, tools, and safety defaults as other surfaces. No verified official configuration was established here that disables every tool/subagent/terminal for this review, so no model call was attempted.
- The official safety notice classifies Harness as experimental and unaudited, and warns that it can execute generated commands and access files, processes, network, and credentials available to it; sandbox and approvals are not guaranteed isolation.

## Official references consulted

- [DeepSeek Harness SAFETY.md](https://github.com/deepseek-ai/deepseek-harness/blob/master/SAFETY.md)
- [Headless profile documentation](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/bundle/headless/README.md)
- [CLI README](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/README.md)

## Scope

No Desktop settings/profile were changed, no credentials were copied, and no project files other than this status report were written. No authentication or model call occurred.
