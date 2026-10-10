import sys

from shop.services import checkout

if __name__ == "__main__":
    print(checkout(sys.argv[1]))
