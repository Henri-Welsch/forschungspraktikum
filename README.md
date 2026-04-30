# Polyglot Monorepo

This repository is a polyglot monorepo, meaning it contains projects written in multiple languages and frameworks within a single repository.

## LaTeX Compilation

To compile the LaTeX code within this repository, you will need a LaTeX distribution and optionally, an IDE plugin for enhanced support.

### LaTeX Distribution

A LaTeX distribution is required to compile the `.tex` files into documents (e.g., PDF). Popular distributions include:

*   **MiKTeX:** A modern TeX distribution, highly recommended for Windows users.
    *   **Download MiKTeX:** [https://miktex.org/](https://miktex.org/)
*   **TeX Live:** A comprehensive cross-platform distribution, commonly used on Linux and macOS.
*   **MacTeX:** A TeX Live-based distribution specifically for macOS.

### IDE Support (Optional)

For an improved development experience, a plugin or software for syntax highlighting, code completion, and compilation integration within your IDE is highly recommended.

*   **TeXiFy-IDEA:** This plugin was used during the development of this repository in an IntelliJ-based IDE (e.g., IntelliJ IDEA, Android Studio). It provides excellent LaTeX support.
    *   **TeXiFy-IDEA Plugin Page:** [https://plugins.jetbrains.com/plugin/9473-texify-idea](https://plugins.jetbrains.com/plugin/9473-texify-idea)

## LaTeX Template

The LaTeX manuscript in this repository uses a template obtained from Springer. You can find the original template and more information at:

*   **Springer LNCS Conference Proceedings Guidelines:** [https://www.springer.com/gp/computer-science/lncs/conference-proceedings-guidelines](https://www.springer.com/gp/computer-science/lncs/conference-proceedings-guidelines)

Look for the "LaTeX2e Proceedings Templates download" under the "Important downloads for authors" section on the Springer website.



Generally speaking, this project is split into 3 modules:
- The one where the Python code lives.
- The one where the LaTeX expose lives.
- The one where the LaTeX paper lives.

When it comes to the structure of the LaTeX codes, the content follows https://kdp.amazon.com/en_US/help/topic/GDDYZG2C7RVF5N9J (Content is split into Front, Body, and Back Matter). Here, `main.tex` under `src` is always the entry point to the document.

For the styling of the LaTeX, we used the following templates:
- https://www.springer.com/gp/computer-science/lncs/conference-proceedings-guidelines
- https://kdp.amazon.com/en_US/help/topic/GDDYZG2C7RVF5N9J