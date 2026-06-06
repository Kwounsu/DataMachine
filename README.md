# DataMachine

Generate test cases and compare Python and C++ implementations to identify inputs that produce different outputs. This tool is useful for finding counterexamples in incorrect solutions and for stress testing competitive programming code.

# How to Use

## 0. Prerequisites

This project is primarily designed to be run from the **Run** button in **VS Code**.

## 1. Project Structure

The expected project structure is:

```text
DataMachine/
├── main.py
└── work/
    ├── _answer.cpp
    ├── _wrong.cpp
    ├── _answer.py
    ├── _wrong.py
    └── generator.py
```

## 2. Preparing the Required Files

Only `main.py` is included in this repository. You must create the remaining files as needed.

### Solution Files

* `_answer.cpp` / `_answer.py`

  * Reference (correct) solutions.
* `_wrong.cpp` / `_wrong.py`

  * Solutions that may contain bugs or incorrect logic.

These files are used when searching for counterexamples. The correct solution can also be used independently to generate expected outputs.

All solution programs must use **standard input** and **standard output**.

You can choose whether to use the Python or C++ versions by modifying the `answer` and `test` settings in `BUTTON_CONFIG`.

### Test Case Generator

`generator.py` is responsible for generating input data and printing it to standard output.

For random test generation, the following imports are commonly used:

```python
from random import randint, shuffle
```

## 3. Compiler Setup

Before using the tool, make sure that:

* Python is installed.
* A C++ compiler is installed.

The default configuration was written using the MinGW compiler included with Code::Blocks. However, you may use any compiler you prefer.

To specify your compiler, update the `compiler` setting in `BUTTON_CONFIG`.

## 4. Configuration

In most cases, the only file you need to modify is `main.py`.

The `mode` option in `BUTTON_CONFIG` can be set to one of the following values:

* `compare`

  * Generates test cases and compares the outputs of the reference and test solutions.
* `generate`

  * Generates input/output data using the reference solution.

A future update may add support for applying predefined generation plans in `generate` mode, but this feature has not yet been implemented.
