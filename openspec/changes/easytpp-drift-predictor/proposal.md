# Proposal

## Why

BehaviorGPT, the predictor used during the hackathon, was trained on shopping, not on how people spend time on a computer. No off-the-shelf model for desktop activity exists. Drift prediction is a question about both *what* comes next and *when*, for example "will I be in a feed within 2 minutes?". Neural temporal point processes model exactly that: event sequences with types and continuous time. [EasyTPP](https://github.com/ant-research/EasyTemporalPointProcess) (ICLR 2024, Apache-2.0, PyTorch) packages 10 such models (RMTPP, NHP, THP, SAHP, AttNHP, IntensityFree, ODETPP, FullyNN, WSMTHP, S2P2) behind one pipeline. Hundreds of thousands of exactly-timed activity sessions are available locally, which is enough to train a small personal model. This change tests whether a model trained on that personal data beats both the baseline and BehaviorGPT. **Scheduled after the hackathon.**

## What Changes

- **Export the timeline as event sequences in EasyTPP's format.** Each split is a list of sequences; each event has `type_event` (0-indexed), `time_since_start`, `time_since_last_event`, `time_since_last_same_event` and `idx_event`, plus `dim_process` (the number of event types). A sequence is one working block, split on idle gaps longer than 30 min.
- **Define a small event-type vocabulary** from the shared labels: mode × drift-prone flag × purpose, plus key apps, roughly 10–40 types. EasyTPP needs a fixed, small type set. The exact vocabulary is a design decision.
- **Train and compare several EasyTPP models** (at least NHP, THP and AttNHP), using the same rolling weekly splits as the backtest, running locally on the Mac. Apple Silicon support isn't documented and needs checking.
- **Turn model output into a drift probability.** Use EasyTPP's thinning-based multi-step prediction to estimate the chance of a consuming / off-task event within the drift window, given the history at the decision point.
- **Wrap the winning model** as a predictor adapter behind the swappable predictor interface (drift-prediction-backtest, D12), and score it with the existing harness on the same drift episodes as BehaviorGPT and the baseline.
- **Report** accuracy, warning lead time, false nudges per day, training cost and prediction latency, and recommend whether it should become the default predictor.
- Out of scope: live nudging, training on anyone else's data, and uploading any data or model weights (training and prediction stay on the Mac).

## Capabilities

### New Capabilities
- `tpp-predictor`: a temporal-point-process drift predictor, covering the event-sequence export, the event-type vocabulary, local training, and turning multi-step samples into a drift probability. It implements the predictor interface.

### Modified Capabilities
(none). This depends on `activity-timeline`, `activity-labeling`, `drift-detection` and `drift-prediction-evaluation`, introduced by `drift-prediction-backtest`, which should be archived first. If that change's requirements need adjusting (for example, exposing session timestamps at the precision TPPs need), list them here when this change is planned.

## Impact

- **New dependency:** `easy-tpp` (PyTorch). It adds a local ML training stack to the project and makes the environment noticeably larger.
- **Compute:** local training on Apple Silicon. Whether PyTorch's Apple GPU backend works with EasyTPP is unverified, and falling back to CPU may limit model size or how many settings can be tried.
- **Data:** reads only the shared timeline and labels. No new data sources, and nothing leaves the Mac. That's an improvement over the cloud predictors.
- **Prerequisite:** `drift-prediction-backtest` must provide the predictor interface, the labeled timeline and the evaluation harness.
