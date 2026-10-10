import { Model } from './base';
import formatPrice, { round2 } from './format';
import * as legacy from './legacy/pricing';
import { v4 as uuid } from 'uuid';

export interface Line {
  sku: string;
  price: number;
  qty: number;
}

export class Cart extends Model {
  private lines: Line[] = [];

  constructor() {
    super(Date.now());
  }

  add = (line: Line) => {
    this.lines.push(line);
    return this;
  };

  total(): number {
    return round2(this.lines.reduce((sum, l) => sum + legacy.lineTotal(l.price, l.qty), 0));
  }

  validate(): boolean {
    return this.lines.length > 0;
  }

  receipt(): string {
    return `${uuid()} ${this.describe()} ${formatPrice(this.total())}`;
  }
}
