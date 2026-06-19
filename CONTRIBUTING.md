# Contributing

[fork]: /fork
[pr]: /compare
[code-of-conduct]: CODE_OF_CONDUCT.md

Hi there! I'm thrilled that you'd like to contribute to this project. Your help is essential for keeping it great.

Please note that this project is released with a [Contributor Code of Conduct][code-of-conduct]. By participating in this project you agree to abide by its terms.

## Prerequisites

- Python 3.12+
- A virtual environment (recommended)

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

4. Push to your fork and [submit a pull request](https://www.google.com/search?q=/compare)

Here are a few things you can do that will increase the likelihood of your pull request being accepted:

- Make sure the pre-commit hooks and `pytest` pass without errors.
- Write and update tests.
- Keep your change as focused as possible. If there are multiple changes you would like to make that are not dependent upon each other, consider submitting them as separate pull requests.
- Write a [good commit message](http://tbaggery.com/2008/04/19/a-note-about-git-commit-messages.html).

Work in Progress pull requests are also welcome to get feedback early on, or if there is something blocking you.

## Resources

- [Home Assistant Developer Docs](https://developers.home-assistant.io/)
- [How to Contribute to Open Source](https://opensource.guide/how-to-contribute/)
- [Using Pull Requests](https://help.github.com/articles/about-pull-requests/)
- [GitHub Help](https://help.github.com)
