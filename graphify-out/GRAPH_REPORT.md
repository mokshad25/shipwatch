# Graph Report - shipwatch  (2026-10-26)

## Corpus Check
- 290 files · ~598,669 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 13 file(s) not represented in the graph (top: (none) 8, .example 1, .jsonl 1)

## Summary
- 1449 nodes · 3337 edges · 84 communities (67 shown, 17 thin omitted)
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 390 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `fdfeb0d2`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- toolbox.py
- models.py
- worker/tests/conftest.py
- webhooks.py
- deploy_demo.py
- build_dataset.py
- Any
- asyncio
- steps
- service.py
- factor_weights
- GitHubClient
- loop.py
- tune_weights.py
- test_migrations.py
- scoring.py
- PrVerdicts
- test_verdicts.py
- argparse
- worker/settings.py
- pytest
- Producer
- sqlalchemy
- FileResponseCache
- Worker
- evalkit.py
- conftest.py
- interval
- ingester/tests/conftest.py
- steps
- create_app
- test_hardening.py
- consumer.py
- test_fixture_replay.py
- load_cached
- pathlib
- EventEnvelope
- crash_test.py
- json
- _main
- services
- test_dlq_replay.py
- run_samples.py
- demoapp/main.py
- shipwatch_common/settings.py
- KafkaLogHandler
- test_outbox.py
- Decimal
- demoapp/tests/conftest.py
- test_app.py
- VerdictRecord
- consume_all
- configure_logging
- DeliveryResult
- v1.md
- logging
- ingester
- demoapp
- environment
- uuid
- v2.md
- env.py
- worker
- verdicts.py
- catalog.py
- postgres
- OutboxRelay
- EvalRecord
- volumes
- shipwatch
- sandbox_pr.sh
- mongo

## God Nodes (most connected - your core abstractions)
1. `GitHubClient` - 39 edges
2. `WorkerSettings` - 38 edges
3. `assess_pr()` - 35 edges
4. `EventEnvelope` - 30 edges
5. `Producer` - 29 edges
6. `make_toolbox()` - 25 edges
7. `Trace` - 22 edges
8. `PrVerdicts` - 22 edges
9. `Worker` - 21 edges
10. `Agent` - 18 edges

## Surprising Connections (you probably didn't know these)
- `Known biases` --references--> `merged_at()`  [INFERRED]
  docs/EVALS.md → evals/build_dataset.py
- `Labeling rules` --references--> `Revert`  [INFERRED]
  docs/EVALS.md → services/worker/src/agent/tools.py
- `Labeling rules` --references--> `revert_target()`  [INFERRED]
  docs/EVALS.md → services/worker/src/agent/tools.py
- `_retrying()` --uses--> `GitHubRateLimited`  [INFERRED]
  evals/build_dataset.py → services/worker/src/agent/github.py
- `list_merged()` --uses--> `GitHubClient`  [INFERRED]
  evals/build_dataset.py → services/worker/src/agent/github.py

## Import Cycles
- None detected.

## Communities (84 total, 17 thin omitted)

### Community 0 - "toolbox.py"
Cohesion: 0.05
Nodes (52): CI regression gate (W5·D5), Dataset (W5·D1), Eval harness (W5·D2), Evals, Known biases, Label spot-check, Labeling rules, Sources (+44 more)

### Community 1 - "models.py"
Cohesion: 0.20
Nodes (17): Base, Outbox, PullRequest, Repo, WebhookEvent, count(), test_concurrent_duplicates_store_one_row(), test_distinct_deliveries_are_all_stored() (+9 more)

### Community 2 - "worker/tests/conftest.py"
Cohesion: 0.06
Nodes (22): ai_text(), ai_tools(), db(), fake_llm(), FakeChatModel, run(), gh_api(), llm_reply() (+14 more)

### Community 3 - "webhooks.py"
Cohesion: 0.13
Nodes (7): DuplicateDeliveryError, enqueue_event(), record_delivery(), _ts(), upsert_pull_request(), github_webhook(), read_body()

### Community 4 - "deploy_demo.py"
Cohesion: 0.07
Nodes (23): Deploy, _now(), DeploySettings, export_commit(), find_pr(), _git_archive(), main(), publish_deploy() (+15 more)

### Community 5 - "build_dataset.py"
Cohesion: 0.13
Nodes (21): build(), _day(), _days(), find_hotfixes(), find_reverts(), has_trouble_reference(), is_bot(), is_revert() (+13 more)

### Community 6 - "Any"
Cohesion: 0.19
Nodes (7): check_gate(), evaluate_pr(), _interleave(), load_dataset(), _round_robin(), run_eval(), one()

### Community 7 - "asyncio"
Cohesion: 0.08
Nodes (27): ProcessedEvent, main(), _main(), pick_request(), run(), Stats, test_first_requests_create_orders_before_any_exist(), test_holds_the_rate_against_the_app() (+19 more)

### Community 8 - "steps"
Cohesion: 0.06
Nodes (31): concurrency, cancel-in-progress, group, GH_TOKEN, GITHUB_TOKEN, GROQ_API_KEY, PR, env (+23 more)

### Community 9 - "service.py"
Cohesion: 0.16
Nodes (10): Limits, AgentRateLimited, assess_pr(), _is_llm_unavailable(), _is_rate_limit(), limits_from(), _reason(), _retry_after() (+2 more)

### Community 10 - "factor_weights"
Cohesion: 0.07
Nodes (29): factor_weights, config_change, failing_ci, hot_files, large_diff, migration, missing_tests, new_author (+21 more)

### Community 11 - "GitHubClient"
Cohesion: 0.17
Nodes (6): GitHubClient, format_run(), main(), run(), WorkerSettings, github()

### Community 12 - "loop.py"
Cohesion: 0.06
Nodes (27): RiskAssessment, Agent, AssessmentParseError, Evidence, _rejected_generation(), _text(), TokenBudgetExceeded, _usage() (+19 more)

### Community 13 - "tune_weights.py"
Cohesion: 0.25
Nodes (9): apply(), changes(), coordinate_search(), f1(), main(), split(), tune(), _with() (+1 more)

### Community 14 - "test_migrations.py"
Cohesion: 0.18
Nodes (9): run_async_migrations(), make_engine(), DatabaseSettings, alembic_config(), run_sql(), schema_diff(), test_0003_backfills_updated_at_from_newest_stored_payload(), updated_at() (+1 more)

### Community 15 - "scoring.py"
Cohesion: 0.09
Nodes (22): RiskFactor, DiffStats, _level(), Levels, load_weights(), Rule, Score, score() (+14 more)

### Community 16 - "PrVerdicts"
Cohesion: 0.17
Nodes (8): AgentRun, Verdict, process(), record_processed(), Source, PrVerdicts, _process(), slow_process()

### Community 17 - "test_verdicts.py"
Cohesion: 0.24
Nodes (15): _event(), head_sha(), make_pipeline(), mongo(), pr_row(), _processed(), recorded_github(), _scored_script() (+7 more)

### Community 18 - "argparse"
Cohesion: 0.31
Nodes (3): compare(), main(), _s()

### Community 19 - "worker/settings.py"
Cohesion: 0.80
Nodes (3): main(), run(), get_settings()

### Community 20 - "pytest"
Cohesion: 0.05
Nodes (45): test_apply_discount(), test_unknown_code_raises(), _error(), GitHubError, GitHubRateLimited, _needs_review(), pr_3783(), _rate_limit() (+37 more)

### Community 21 - "Producer"
Cohesion: 0.14
Nodes (6): Producer, on_delivery(), test_full_local_queue_is_reported_as_a_failed_delivery(), produce(), _produce(), _make()

### Community 22 - "sqlalchemy"
Cohesion: 0.15
Nodes (5): upgrade(), upgrade(), upgrade(), upgrade(), upgrade()

### Community 23 - "FileResponseCache"
Cohesion: 0.18
Nodes (3): FileResponseCache, ResponseCache, test_file_cache_replays_and_offline_mode_refuses_the_network()

### Community 24 - "Worker"
Cohesion: 0.07
Nodes (5): DeadLetterError, Worker, Claim, EventGate, InjectedFailure

### Community 25 - "evalkit.py"
Cohesion: 0.14
Nodes (17): metrics_table(), _num(), render_markdown(), _jsonl(), _rate_limit(), recorded(), _report(), _settings() (+9 more)

### Community 26 - "conftest.py"
Cohesion: 0.20
Nodes (6): kafka_topic(), make_topic(), _make(), delete_topics(), ensure_topics(), test_ensure_topics_is_idempotent()

### Community 27 - "interval"
Cohesion: 0.21
Nodes (18): healthcheck, interval, retries, test, timeout, healthcheck, healthcheck, healthcheck (+10 more)

### Community 28 - "ingester/tests/conftest.py"
Cohesion: 0.23
Nodes (6): client(), db(), make_pr_payload(), pr_payload(), send(), _send()

### Community 29 - "steps"
Cohesion: 0.11
Nodes (17): DATABASE_URL, jobs, lint-test, env, runs-on, steps, name, branches (+9 more)

### Community 30 - "create_app"
Cohesion: 0.25
Nodes (6): app(), create_app(), create_order(), get_order(), jitter(), list_products()

### Community 31 - "test_hardening.py"
Cohesion: 0.11
Nodes (20): sign(), verify_signature(), count_events(), log_lines(), signed_headers(), test_body_at_limit_is_accepted(), test_body_over_limit_is_413(), test_every_log_line_of_a_request_has_delivery_id() (+12 more)

### Community 33 - "test_fixture_replay.py"
Cohesion: 0.36
Nodes (7): count(), get_pr(), load(), replay(), test_fixture_is_a_real_pull_request_delivery(), test_lifecycle_replay_tracks_pull_request_state(), test_replaying_every_fixture_twice_keeps_one_row_each()

### Community 34 - "load_cached"
Cohesion: 0.24
Nodes (6): agent_cache_path(), load_cached(), make_github(), _offline(), slug(), write_report()

### Community 35 - "pathlib"
Cohesion: 0.07
Nodes (20): fixture_name(), main(), make_handler(), do_POST(), _relay(), _save(), test_fixture_name(), test_fixture_file_uses_its_event_and_compact_body() (+12 more)

### Community 36 - "EventEnvelope"
Cohesion: 0.23
Nodes (7): EventEnvelope, test_envelope_defaults_are_fresh_and_utc(), test_envelope_round_trips_through_bytes(), test_envelope_wire_format_has_the_spec_fields(), test_invalid_envelope_is_rejected(), test_relay_publishes_to_redpanda_keyed_by_repo(), test_validation_error_is_not_retryable()

### Community 37 - "crash_test.py"
Cohesion: 0.23
Nodes (9): compose(), db_counts(), dlq_event_ids(), main(), produce(), record(), redis_skips(), Result (+1 more)

### Community 38 - "json"
Cohesion: 0.06
Nodes (24): DlqEntry, group_by_error(), main(), print_groups(), read_dlq(), replay(), select_for_replay(), dead_letter_record() (+16 more)

### Community 40 - "_main"
Cohesion: 0.27
Nodes (4): eval_settings(), _count(), main(), _main()

### Community 41 - "services"
Cohesion: 0.15
Nodes (14): redpanda, KAFKA_BROKERS, condition, depends_on, environment, image, ports, command (+6 more)

### Community 42 - "test_dlq_replay.py"
Cohesion: 0.24
Nodes (5): entry(), test_flags_other_than_replay_need_replay(), test_group_by_error_breaks_ties_by_name_and_handles_empty(), test_replay_end_to_end(), test_select_for_replay_filters_then_limits_oldest_first()

### Community 43 - "run_samples.py"
Cohesion: 0.26
Nodes (8): load_samples(), main(), recent_merged(), run_samples(), Sample, _save(), summary_table(), top_factor()

### Community 44 - "demoapp/main.py"
Cohesion: 0.32
Nodes (3): OrderIn, OrderLine, DemoSettings

### Community 45 - "shipwatch_common/settings.py"
Cohesion: 0.18
Nodes (5): get_settings(), ServiceSettings, Settings, test_strong_webhook_secret_is_accepted(), test_weak_webhook_secret_is_rejected()

### Community 46 - "KafkaLogHandler"
Cohesion: 0.27
Nodes (5): KafkaLogHandler, _record(), test_full_queue_drops_instead_of_raising(), test_rdkafka_records_are_not_shipped(), test_ships_formatted_lines_keyed_by_service()

### Community 47 - "test_outbox.py"
Cohesion: 0.27
Nodes (9): FakeProducer, outbox_rows(), test_duplicate_and_ignored_deliveries_write_no_outbox_row(), test_failure_keeps_failed_row_and_everything_after_it(), test_outbox_row_rolls_back_with_the_delivery(), test_pull_request_delivery_writes_one_outbox_row(), test_relay_publishes_in_order_and_marks_sent(), test_relay_skips_while_another_relay_holds_the_lock() (+1 more)

### Community 48 - "Decimal"
Cohesion: 0.27
Nodes (4): apply_discount(), discount_rate(), subtotal(), UnknownDiscountCode

### Community 49 - "demoapp/tests/conftest.py"
Cohesion: 0.25
Nodes (5): app(), CaptureHandler, client(), logs(), settings()

### Community 50 - "test_app.py"
Cohesion: 0.21
Nodes (5): test_create_and_get_order(), test_missing_order_is_404_and_warns(), test_out_of_stock_is_409_and_warns(), test_slow_request_warns(), test_unhandled_error_is_500_with_a_structured_traceback()

### Community 51 - "VerdictRecord"
Cohesion: 0.29
Nodes (3): VerdictRecord, summary_message(), VerdictPublishError

### Community 53 - "configure_logging"
Cohesion: 0.22
Nodes (3): _add_static(), configure_logging(), json_formatter()

### Community 54 - "DeliveryResult"
Cohesion: 0.15
Nodes (3): DeliveryResult, ProducerLike, ProducerLike

### Community 56 - "logging"
Cohesion: 0.27
Nodes (5): get_logger(), _Capture, test_defaults_add_nothing(), test_static_fields_and_extra_handlers(), lifespan()

### Community 57 - "ingester"
Cohesion: 0.24
Nodes (10): GIT_SHA, args, context, dockerfile, build, build, env_file, ports (+2 more)

### Community 58 - "demoapp"
Cohesion: 0.20
Nodes (10): depends_on, ports, profiles, stop_grace_period, postgres, redpanda-init, depends_on, condition (+2 more)

### Community 59 - "environment"
Cohesion: 0.24
Nodes (10): environment, CONSUMER_INSTANCE_ID, DATABASE_URL, FAIL_RATE, KAFKA_BOOTSTRAP_SERVERS, LOG_LEVEL, MONGO_URL, REDIS_URL (+2 more)

### Community 60 - "uuid"
Cohesion: 0.27
Nodes (4): db_engine(), Delivery, load_delivery(), main()

### Community 63 - "worker"
Cohesion: 0.22
Nodes (9): mongo, redis, condition, condition, worker, depends_on, env_file, restart (+1 more)

### Community 64 - "verdicts.py"
Cohesion: 0.15
Nodes (6): LLMConfigError, make_chat_model(), _settings(), test_groq_settings(), test_model_is_required(), test_provider_picks_the_chat_model()

### Community 66 - "catalog.py"
Cohesion: 0.33
Nodes (3): Product, _products(), Store

### Community 67 - "postgres"
Cohesion: 0.25
Nodes (8): POSTGRES_DB, POSTGRES_PASSWORD, POSTGRES_USER, environment, image, ports, volumes, postgres

### Community 68 - "OutboxRelay"
Cohesion: 0.20
Nodes (4): create_app(), lifespan(), OutboxRelay, test_relay_loop_publishes_after_wake_and_stops()

### Community 71 - "EvalRecord"
Cohesion: 0.16
Nodes (8): build_report(), classification(), EvalRecord, metrics(), percentile(), predicted(), RunResult, top_factor()

### Community 75 - "volumes"
Cohesion: 0.40
Nodes (4): volumes, mongodata, pgdata, redpanda

### Community 76 - "shipwatch"
Cohesion: 0.70
Nodes (5): demoapp, ingester, shipwatch, shipwatch-common, worker

### Community 77 - "sandbox_pr.sh"
Cohesion: 0.60
Nodes (3): die(), sandbox_pr.sh script, step()

### Community 79 - "mongo"
Cohesion: 0.50
Nodes (4): image, ports, volumes, mongo

## Knowledge Gaps
- **17 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `GitHubClient` connect `GitHubClient` to `toolbox.py`, `verdicts.py`, `load_cached`, `build_dataset.py`, `Any`, `service.py`, `run_samples.py`, `PrVerdicts`, `pytest`, `FileResponseCache`?**
  _High betweenness centrality (0.032) - this node is a cross-community bridge._
- **Are the 24 inferred relationships involving `GitHubClient` (e.g. with `build()` and `has_trouble_reference()`) actually correct?**
  _`GitHubClient` has 24 INFERRED edges - model-reasoned connections that need verification._
- **Should `toolbox.py` be split into smaller, more focused modules?**
  _Cohesion score 0.05369369369369369 - nodes in this community are weakly interconnected._
- **Why does `WorkerSettings` connect `GitHubClient` to `verdicts.py`, `consumer.py`, `load_cached`, `worker/tests/conftest.py`, `build_dataset.py`, `Any`, `_main`, `service.py`, `run_samples.py`, `shipwatch_common/settings.py`, `PrVerdicts`, `worker/settings.py`, `pytest`, `FileResponseCache`, `Worker`, `evalkit.py`?**
  _High betweenness centrality (0.024) - this node is a cross-community bridge._
- **Are the 24 inferred relationships involving `WorkerSettings` (e.g. with `_main()` and `eval_settings()`) actually correct?**
  _`WorkerSettings` has 24 INFERRED edges - model-reasoned connections that need verification._
- **Should `worker/tests/conftest.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06448979591836734 - nodes in this community are weakly interconnected._
- **Why does `EventEnvelope` connect `EventEnvelope` to `consumer.py`, `worker/tests/conftest.py`, `webhooks.py`, `OutboxRelay`, `crash_test.py`, `json`, `test_outbox.py`, `PrVerdicts`, `test_verdicts.py`, `Producer`, `Worker`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._