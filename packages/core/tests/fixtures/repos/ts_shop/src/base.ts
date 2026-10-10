export abstract class Model {
  constructor(public id: number) {}

  describe(): string {
    return `${this.constructor.name}#${this.id}`;
  }

  abstract validate(): boolean;
}
