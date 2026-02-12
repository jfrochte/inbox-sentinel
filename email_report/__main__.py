"""
__main__.py -- Entry point: starts the GUI server.

Usage: python -m email_report
"""

import uvicorn


def main():
    uvicorn.run("gui.server:app", host="127.0.0.1", port=8741, log_level="info")


if __name__ == "__main__":
    main()
