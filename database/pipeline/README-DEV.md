# Pipeline — developer guide

Pipeline code transforms source artifacts into immutable release inputs. Keep
each stage idempotent, record tool/model/prompt hashes, and validate output
schemas before the next stage. Use a staging dataset and never point workers at
the production pointer while rebuilding.
