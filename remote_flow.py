from metaflow import FlowSpec, step, Parameter, resources, environment, kubernetes, current, card
from mixins import HuggingFaceLora, N_GPU, visible_devices
from metaflow.metaflow_config import DATASTORE_SYSROOT_S3
from custom_decorators import pip, gpu_profile
import os
from model_store import ModelStore

class ModelCacheParams:
    model_cache_s3_base_path = Parameter(
        "cache-s3-base-path",
        help="By default this will use the `metaflow.metaflow_config.DATASTORE_SYSROOT_S3` ie the `METAFLOW_DATASTORE_SYSROOT_S3` configuration variable and use the path to it's parent directory. You can override this by specifying a different path here.",
        default=os.path.dirname(DATASTORE_SYSROOT_S3),
    )

    @property
    def runtime_models_root(self):
        return os.path.join(self.model_cache_s3_base_path, "trained-models", current.flow_name, current.run_id, current.step_name, current.task_id)

    @property
    def hf_models_cache_root(self):
        return os.path.join(self.model_cache_s3_base_path, "huggingface-models")


class LlamaInstructionTuning(FlowSpec, HuggingFaceLora, ModelCacheParams):

    push_checkpoints = Parameter(
        "push", help="push checkpoints on huggingface", default=False, type=bool
    )

    @step
    def start(self):
        store = ModelStore(
            model_store_root = self.hf_models_cache_root
        )
        import tempfile
        if not store.already_exists(self.base_model):
            with tempfile.TemporaryDirectory() as tmpdirname:
                self.download_model_from_huggingface(tmpdirname)
                store.upload(tmpdirname, self.base_model)
        self.next(self.finetune)

    @environment(
        vars={
            "CUDA_VISIBLE_DEVICES": visible_devices,
            "WORLD_SIZE": N_GPU,
        }
    )
    @gpu_profile(interval=1)
    @kubernetes(image="valayob/hf-transformer-gpu:4.29.2.3", gpu=N_GPU, cpu=16, memory=72000)
    @card
    @step
    def finetune(self):
        hf_model_store = ModelStore(
            model_store_root=self.hf_models_cache_root
        )
        self.trained_model_path = self.runtime_models_root
        trained_model_store = ModelStore(
            model_store_root=self.trained_model_path
        )
        import os
        import tempfile
        if not hf_model_store.already_exists(self.base_model):
            raise ValueError(f"Model {self.base_model} not found in the model store. This shouldn't happen.")
        with tempfile.TemporaryDirectory() as tmpdirname:
            hf_model_store.download(self.base_model, tmpdirname)
            self.run(base_model_path=tmpdirname)
            trained_model_store.upload(self.model_save_directory, self.base_model)
            
        self.next(self.end)

    @step
    def end(self):
        if not self.push_checkpoints:
            print("Completed!")
            return 
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdirname:
            store = ModelStore(
                model_store_root=self.trained_model_path
            )
            store.download(self.base_model, tmpdirname)
            self.upload_to_huggingface(model_directory=tmpdirname)


if __name__ == "__main__":
    LlamaInstructionTuning()

# LOCAL: python flow.py run
# REMOTE: python flow.py --package-suffixes=.txt,.json run --with batch
