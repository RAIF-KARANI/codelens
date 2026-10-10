import React, { useState } from 'react';
import { Cart, formatPrice } from '..';

export function CartView({ cart }: { cart: Cart }) {
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen(!open);
  return (
    <div onClick={toggle}>
      {open && <span>{formatPrice(cart.total())}</span>}
    </div>
  );
}

export default React.memo(CartView);
