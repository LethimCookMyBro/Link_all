# Runbook: Baseline test contract drift

- **Symptom**: Baseline pytest had five failures: Discord helper calls were
  asserted while the webhook was empty, and gateway tests patched a retired
  `discord_bot.bot` object instead of the live client.
- **Root cause**: The tests no longer matched the production contracts.
  `C2.C2` intentionally treats an empty `DISCORD_WEBHOOK` as a no-op;
  `discord_bot.main()` used an import-time token rather than the configurable
  runtime token; and the old server IP expectation was obsolete.
- **Detection**: Run
  `tests/test_c2_coverage.py::C2DeepCoverageTests::test_discord_helpers`,
  `tests/test_error_handling_edge_cases.py::DiscordBotGatewayTests::test_empty_token_skips_start`,
  `tests/test_error_handling_edge_cases.py::DiscordBotGatewayTests::test_login_failure_swallowed`,
  `tests/test_error_handling_edge_cases.py::DiscordBotGatewayTests::test_generic_gateway_error_swallowed`,
  and `tests/test_safe_refactor_helpers.py::Milestone2Tests::test_no_hardcoded_ips_in_c2_urls`.
- **Fix**: Helper-call assertions supply a dummy webhook and patch the module
  request client; gateway tests patch `discord_bot.client.start`, while
  `main()` reads `config.DISCORD_BOT_TOKEN` at call time and skips blank values.
  The IP guard now rejects the retired literal and verifies the environment
  sourced `SERVER_IP` declaration.
