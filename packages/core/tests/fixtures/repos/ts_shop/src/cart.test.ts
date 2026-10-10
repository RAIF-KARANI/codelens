import { Cart } from './cart';

describe('Cart', () => {
  it('totals lines', () => {
    const cart = new Cart();
    cart.add({ sku: 'a', price: 2, qty: 3 });
    expect(cart.total()).toBe(6);
  });
});
