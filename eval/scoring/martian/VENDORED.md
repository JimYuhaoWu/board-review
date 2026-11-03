# Vendored Martian code

Unmodified copies from the Martian Code Review Bench (MIT, see `LICENSE`), commit `e616e849755441da38f18bf3adba2c9583b03803`. The scoring wrapper imports their prompts, LLM-call functions, `evaluate_review`, and profile definitions, so scores follow Martian's method exactly.

Do not edit these files. `eval/scoring/tests/test_vendored.py` fails if any of them changes.

| File | Upstream path | SHA-256 (LF line endings) |
|---|---|---|
| `step2_extract_comments.py` | `offline/code_review_benchmark/step2_extract_comments.py` | `d0b328d3953a3f68819ed530b1aafb594e7ad7cf09ef64e3b7e82d7bb221c800` |
| `step2_5_dedup_candidates.py` | `offline/code_review_benchmark/step2_5_dedup_candidates.py` | `5438ffb2a487129bd0f85d5a8c43020efe5027ec2d1d51e599b9c1d9f743cd4c` |
| `step3_judge_comments.py` | `offline/code_review_benchmark/step3_judge_comments.py` | `7370dfb21bed01655aada1d4d75f32e07c92e4c4c86dfc6cf6d47058a65ce16d` |
| `score_profiles.py` | `offline/analysis/score_profiles.py` | `b5d474a373f008f0899dbcbe8deca5f596d8081bc9579b75391971f6f2959f52` |
| `LICENSE` | `LICENSE` | `3d0f7aacf358c3578c1e541bdd297b675d76805e6bf3dddaf89b6d2b45e3afad` |
