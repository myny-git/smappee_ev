# Contributing

[fork]: /fork
[pr]: /compare
[code-of-conduct]: CODE_OF_CONDUCT.md

Hi there! I'm thrilled that you'd like to contribute to this project. Your help is essential for keeping it great.

Please note that this project is released with a [Contributor Code of Conduct][code-of-conduct]. By participating in this project you agree to abide by its terms.

## Prerequisites

- Python 3.14.2+ for the pinned Home Assistant test dependencies
- A virtual environment (recommended)

The shipped integration keeps Python 3.13-compatible syntax. CI checks that
separately from the tests running against the Home Assistant version pinned in
`requirements-test.txt`.

## Setup

1. [Fork][fork] and clone the repository
2. Create and activate a virtual environment:

   ```bash
   python -m venv venv
   source venv/bin/activate
   ```

3. Install test/dev dependencies:

   ```bash
   pip install -r requirements-test.txt
   ```

4. Install pre-commit hooks:

   ```bash
   pip install pre-commit
   pre-commit install
   ```

## Submitting a pull request

1. Create a new branch: `git checkout -b my-branch-name`
2. Make your changes. Your code will be automatically checked and formatted when you commit.
*If you need to run the checks manually:*

   ```bash
   pre-commit run --all-files
   ```

3. Run the tests:

   ```bash
   pytest
   ```

4. Push to your fork and [submit a pull request][pr]

Here are a few things you can do that will increase the likelihood of your pull request being accepted:

- Make sure the pre-commit hooks and `pytest` pass without errors.
- Write and update tests.
- Keep your change as focused as possible. If there are multiple changes you would like to make that are not dependent upon each other, consider submitting them as separate pull requests.
- Write a [good commit message](http://tbaggery.com/2008/04/19/a-note-about-git-commit-messages.html).

Work in Progress pull requests are also welcome to get feedback early on, or if there is something blocking you.

## State updates

See [state update rules](docs/state-updates.md) before changing coordinator merges,
control actions or MQTT measurement handling. Regression tests intentionally block
network calls while newer MQTT data, commands and sessions arrive.

## Real MQTT smoke test

The ordinary tests stub `aiomqtt` because the Home Assistant pytest plugin pins an
incompatible `paho-mqtt`. CI also runs a separate smoke test with real `aiomqtt`
and a disposable Mosquitto container. It covers subscriptions, retained messages,
payload handling, bursts across multiple topics, reconnect and shutdown during
reconnect. TLS certificate validation is outside this local transport test.

With Docker running, use a separate virtual environment:

```bash
python -m venv .venv-mqtt
source .venv-mqtt/bin/activate
pip install -r requirements-mqtt-test.txt
python -m unittest discover -s mqtt_smoke -v
```

The test exposes its broker on localhost only, uses a random port and removes
its own container on completion.

## Resources

- [Home Assistant Developer Docs](https://developers.home-assistant.io/)
- [How to Contribute to Open Source](https://opensource.guide/how-to-contribute/)
- [Using Pull Requests](https://help.github.com/articles/about-pull-requests/)
- [GitHub Help](https://help.github.com)
