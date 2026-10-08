/**
 * Kafka event consumer for streaming events to WebSocket clients.
 *
 * Subscribes to event topics from the Python worker and relays them over
 * WebSocket to connected clients. Handles consumer group coordination and
 * graceful shutdown.
 */
import { Kafka, type Consumer, type EachMessagePayload, logLevel } from "kafkajs";
import type { Logger } from "pino";

import type { Env } from "../env";

interface ClientHandler {
  (event: {
    topic: string;
    key: string | null;
    value: Record<string, unknown>;
    timestamp: string;
  }): void;
}

let instance: KafkaEventConsumer | null = null;

export class KafkaEventConsumer {
  private kafka: Kafka;
  private consumer: Consumer | null = null;
  private clientHandlers = new Set<ClientHandler>();
  private running = false;

  constructor(
    private brokers: string[],
    private logger: Logger,
  ) {
    this.kafka = new Kafka({
      clientId: "commitmail-server",
      brokers: this.brokers,
      logLevel: logLevel.ERROR,
      logCreator: () => {
        return ({ level, log }) => {
          const { timestamp, message } = log;
          if (level >= 30) {  // ERROR or worse
            this.logger.error({ kafka: true, timestamp }, message);
          }
        };
      },
    });
  }

  static getInstance(brokers: string[], logger: Logger): KafkaEventConsumer {
    if (!instance) {
      instance = new KafkaEventConsumer(brokers, logger);
    }
    return instance;
  }

  async connect(): Promise<void> {
    if (this.running) return;

    try {
      this.consumer = this.kafka.consumer({ groupId: "commitmail-web-server" });
      await this.consumer.connect();
      await this.consumer.subscribe({
        topics: [/^events\..+/],
        fromBeginning: false,
      });

      await this.consumer.run({
        eachMessage: this.handleMessage.bind(this),
      });

      this.running = true;
      this.logger.info("Kafka consumer connected and subscribed to events");
    } catch (error) {
      this.logger.error({ error }, "Failed to connect Kafka consumer");
      throw error;
    }
  }

  async disconnect(): Promise<void> {
    if (this.consumer) {
      await this.consumer.disconnect();
      this.consumer = null;
      this.running = false;
      this.logger.info("Kafka consumer disconnected");
    }
  }

  registerClient(handler: ClientHandler): () => void {
    this.clientHandlers.add(handler);
    this.logger.debug(
      { clients: this.clientHandlers.size },
      "Client registered for events",
    );

    return () => {
      this.clientHandlers.delete(handler);
      this.logger.debug(
        { clients: this.clientHandlers.size },
        "Client unregistered from events",
      );
    };
  }

  private async handleMessage({
    topic,
    partition,
    message,
  }: EachMessagePayload): Promise<void> {
    try {
      const value =
        message.value ? JSON.parse(message.value.toString()) : null;
      const key = message.key?.toString() ?? null;

      const event = {
        topic,
        key,
        value,
        timestamp: new Date(Number(message.timestamp)).toISOString(),
      };

      this.clientHandlers.forEach((handler) => {
        try {
          handler(event);
        } catch (err) {
          this.logger.error({ error: err }, "Error sending event to client");
        }
      });

      this.logger.debug(
        { topic, partition, clients: this.clientHandlers.size },
        "Event distributed",
      );
    } catch (error) {
      this.logger.error({ error, topic }, "Failed to parse event message");
    }
  }

  isRunning(): boolean {
    return this.running;
  }
}

export async function initializeKafkaConsumer(
  env: Env,
  logger: Logger,
): Promise<KafkaEventConsumer | null> {
  if (!env.KAFKA_BROKERS || env.KAFKA_BROKERS.length === 0) {
    logger.info("Kafka brokers not configured; event streaming disabled");
    return null;
  }

  const consumer = KafkaEventConsumer.getInstance(env.KAFKA_BROKERS, logger);
  try {
    await consumer.connect();
    return consumer;
  } catch (error) {
    logger.warn({ error }, "Kafka consumer failed to start; proceeding without event streaming");
    return null;
  }
}
