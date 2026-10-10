from shop.models import Product


def test_gross_includes_tax():
    product = Product(id=1, name="pen", price=10.0)
    assert product.gross == 12.0
