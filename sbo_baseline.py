import os

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
from smac import Scenario
from smac import HyperparameterOptimizationFacade as HPOFacade
from smac.initial_design.sobol_design import SobolInitialDesign
from smac.acquisition.maximizer.differential_evolution import DifferentialEvolution
from smac.random_design.probability_design import ProbabilityRandomDesign
from smac.acquisition.function.abstract_acquisition_function import AbstractAcquisitionFunction
from smac.intensifier.intensifier import Intensifier
from smac.runhistory.encoder.encoder import RunHistoryEncoder
from smac.main.config_selector import ConfigSelector
from ConfigSpace import ConfigurationSpace, Float, Configuration
from src.sbo import Oracle, SaveAngleCallback, SurrogateModel
from src.models import DenseModel
from src.dataloaders import BaselineDataLoader
from src.constants import INPUT_SHAPE, OUTPUT_SHAPE
import tensorflow as tf
import math
import numpy as np

oracle = Oracle()
seed = 0
ex_name = 'test_baseline'


def p_norm(matrix, p=4):
    return float(tf.norm(matrix, ord=p))


cs = ConfigurationSpace(seed=seed)
for i in range(INPUT_SHAPE[0]):
    x = Float(f"{i:02}", (0.01, 1))
    cs.add_hyperparameters([x])

scenario = Scenario(cs,
                    name=ex_name,
                    deterministic=True,
                    n_trials=150)


def obj_function(config: Configuration, seed: int = seed) -> float:
    x = []
    for i in range(INPUT_SHAPE[0]):
        x_i = config[f"{i:02}"]
        x.append(x_i)
    y = oracle.simulate(x)

    return p_norm(y)


model = DenseModel(name='Baseline',
                   input_dim=INPUT_SHAPE,
                   output_dim=(math.prod(OUTPUT_SHAPE),))
data_loader = BaselineDataLoader('data/val_short.csv')
sur_model = SurrogateModel(cs, model, data_loader, p_norm, oracle, n_inferences=1)


class ObjFunctionAcquisition(AbstractAcquisitionFunction):
    def __init__(self):
        super(ObjFunctionAcquisition, self).__init__()

    def _compute(self, X: np.ndarray) -> np.ndarray:
        m, _ = self._model.predict_marginalized(X)
        return -m


smac = HPOFacade(
    scenario=scenario,
    target_function=obj_function,
    model=sur_model,
    acquisition_function=ObjFunctionAcquisition(),
    acquisition_maximizer=DifferentialEvolution(
        configspace=scenario.configspace,
        challengers=1000,
        seed=scenario.seed,
    ),
    initial_design=SobolInitialDesign(
        scenario=scenario,
        n_configs=100,
        max_ratio=1,
        seed=scenario.seed,
    ),
    random_design=ProbabilityRandomDesign(seed=scenario.seed, probability=0.08447232371720552),
    intensifier=Intensifier(
        scenario=scenario,
        max_config_calls=3,
        max_incumbents=20,
    ),
    runhistory_encoder=RunHistoryEncoder(scenario),
    config_selector=ConfigSelector(scenario, retrain_after=10),
    overwrite=True,
    logging_level=20,
    callbacks=[SaveAngleCallback(oracle, f'{ex_name}/{seed}')]
)

incumbent = smac.optimize()

# Get cost of default configuration
default_cost = smac.validate(cs.get_default_configuration())
print(f"Default cost: {default_cost}")

# Let's calculate the cost of the incumbent
incumbent_cost = smac.validate(incumbent)
print(f"Incumbent cost: {incumbent_cost}")
