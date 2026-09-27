"""CLI入口: python -m latch.auth → tools.main()"""

import sys

from latch.auth.tools import main

if __name__ == "__main__":
    sys.exit(main())
