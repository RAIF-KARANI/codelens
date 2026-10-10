const { round2 } = require('../format');

function lineTotal(price, qty) {
  return round2(price * qty);
}

module.exports = { lineTotal };
