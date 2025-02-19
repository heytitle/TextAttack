"""
Reimplementation of search method from Generating Natural Language Adversarial Examples 
by Alzantot et. al
`<arxiv.org/abs/1804.07998>`_
`<github.com/nesl/nlp_adversarial_examples>`_
"""

from copy import deepcopy

import numpy as np
import torch

from textattack.goal_function_results import GoalFunctionResultStatus
from textattack.search_methods import SearchMethod
from textattack.shared.validators import transformation_consists_of_word_swaps


class GeneticAlgorithm(SearchMethod):
    """
    Attacks a model with word substiutitions using a genetic algorithm.

    Args:
        pop_size (int): The population size. Defaults to 20. 
        max_iters (int): The maximum number of iterations to use. Defaults to 50.
        temp (float): Temperature for softmax function used to normalize probability dist when sampling parents.
            Higher temperature increases the sensitivity to lower probability candidates.
        give_up_if_no_improvement (bool): If True, stop the search early if no candidate that improves the score is found.
        max_crossover_retries (int): Maximum number of crossover retries if resulting child fails to pass the constraints.
            Setting it to 0 means we immediately take one of the parents at random as the child. 
    """

    def __init__(
        self,
        pop_size=20,
        max_iters=50,
        temp=0.3,
        give_up_if_no_improvement=False,
        max_crossover_retries=20,
    ):
        self.max_iters = max_iters
        self.pop_size = pop_size
        self.temp = temp
        self.give_up_if_no_improvement = give_up_if_no_improvement
        self.max_crossover_retries = max_crossover_retries

        # internal flag to indicate if search should end immediately
        self._search_over = False

    def _perturb(self, pop_member, original_result):
        """
        Replaces a word in pop_member that has not been modified in place.
        Args:
            pop_member (PopulationMember): The population member being perturbed.
            original_result (GoalFunctionResult): Result of original sample being attacked
            
        Returns: None
        """
        num_words = pop_member.num_candidates_per_word.shape[0]
        num_candidates_per_word = np.copy(pop_member.num_candidates_per_word)
        non_zero_indices = np.count_nonzero(num_candidates_per_word)
        if non_zero_indices == 0:
            return
        iterations = 0
        while iterations < non_zero_indices:
            w_select_probs = num_candidates_per_word / np.sum(num_candidates_per_word)
            rand_idx = np.random.choice(num_words, 1, p=w_select_probs)[0]

            transformations = self.get_transformations(
                pop_member.attacked_text,
                original_text=original_result.attacked_text,
                indices_to_modify=[rand_idx],
            )

            if not len(transformations):
                iterations += 1
                continue

            new_results, self._search_over = self.get_goal_results(transformations)

            if self._search_over:
                break

            diff_scores = (
                torch.Tensor([r.score for r in new_results]) - pop_member.result.score
            )
            if len(diff_scores) and diff_scores.max() > 0:
                idx = diff_scores.argmax()
                pop_member.attacked_text = transformations[idx]
                pop_member.num_candidates_per_word[rand_idx] = 0
                pop_member.results = new_results[idx]
                break

            num_candidates_per_word[rand_idx] = 0
            iterations += 1

    def _crossover(self, pop_member1, pop_member2, original_result):
        """
        Generates a crossover between pop_member1 and pop_member2.
        If the child fails to satisfy the constraits, we re-try crossover for a fix number of times,
        before taking one of the parents at random as the resulting child.
        Args:
            pop_member1 (PopulationMember): The first population member.
            pop_member2 (PopulationMember): The second population member.
        Returns:
            A population member containing the crossover.
        """
        x1_text = pop_member1.attacked_text
        x2_text = pop_member2.attacked_text
        x2_words = x2_text.words

        num_tries = 0
        passed_constraints = False
        while num_tries < self.max_crossover_retries + 1:
            indices_to_replace = []
            words_to_replace = []
            num_candidates_per_word = np.copy(pop_member1.num_candidates_per_word)
            for i in range(len(x1_text.words)):
                if np.random.uniform() < 0.5:
                    indices_to_replace.append(i)
                    words_to_replace.append(x2_words[i])
                    num_candidates_per_word[i] = pop_member2.num_candidates_per_word[i]
            new_text = x1_text.replace_words_at_indices(
                indices_to_replace, words_to_replace
            )
            if "last_transformation" in x1_text.attack_attrs:
                new_text.attack_attrs["last_transformation"] = x1_text.attack_attrs[
                    "last_transformation"
                ]
                filtered = self.filter_transformations(
                    [new_text], x1_text, original_text=original_result.attacked_text
                )
            elif "last_transformation" in x2_text.attack_attrs:
                new_text.attack_attrs["last_transformation"] = x2_text.attack_attrs[
                    "last_transformation"
                ]
                filtered = self.filter_transformations(
                    [new_text], x1_text, original_text=original_result.attacked_text
                )
            else:
                # In this case, neither x_1 nor x_2 has been transformed,
                # meaning that new_text == original_text
                filtered = [new_text]

            if filtered:
                new_text = filtered[0]
                passed_constraints = True
                break

            num_tries += 1

        if not passed_constraints:
            # If we cannot find a child that passes the constraints,
            # we just randomly pick one of the parents to be the child for the next iteration.
            new_text = (
                pop_member1.attacked_text
                if np.random.uniform() < 0.5
                else pop_member2.attacked_text
            )

        new_results, self._search_over = self.get_goal_results([new_text])

        return PopulationMember(new_text, num_candidates_per_word, new_results[0])

    def _initialize_population(self, initial_result):
        """
        Initialize a population of texts each with one word replaced
        Args:
            initial_result (GoalFunctionResult): The result to instantiate the population with
        Returns:
            The population.
        """
        words = initial_result.attacked_text.words
        num_candidates_per_word = np.zeros(len(words))
        transformations = self.get_transformations(
            initial_result.attacked_text, original_text=initial_result.attacked_text
        )
        for transformed_text in transformations:
            diff_idx = initial_result.attacked_text.first_word_diff_index(
                transformed_text
            )
            num_candidates_per_word[diff_idx] += 1

        # Just b/c there are no candidates now doesn't mean we never want to select the word for perturbation
        # Therefore, we give small non-zero probability for words with no candidates
        # Epsilon is some small number to approximately assign 1% probability
        num_total_candidates = np.sum(num_candidates_per_word)
        epsilon = max(1, int(num_total_candidates * 0.01))
        for i in range(len(num_candidates_per_word)):
            if num_candidates_per_word[i] == 0:
                num_candidates_per_word[i] = epsilon

        population = []
        for _ in range(self.pop_size):
            pop_member = PopulationMember(
                initial_result.attacked_text,
                np.copy(num_candidates_per_word),
                initial_result,
            )
            # Perturb `pop_member` in-place
            self._perturb(pop_member, initial_result)
            population.append(pop_member)
        return population

    def _perform_search(self, initial_result):
        self._search_over = False
        population = self._initialize_population(initial_result)
        current_score = initial_result.score
        for i in range(self.max_iters):
            population = sorted(population, key=lambda x: x.result.score, reverse=True)
            if (
                self._search_over
                or population[0].result.goal_status
                == GoalFunctionResultStatus.SUCCEEDED
            ):
                break

            if population[0].result.score > current_score:
                current_score = population[0].result.score
            elif self.give_up_if_no_improvement:
                break

            pop_scores = torch.Tensor([pm.result.score for pm in population])
            logits = ((-pop_scores) / self.temp).exp()
            select_probs = (logits / logits.sum()).cpu().numpy()

            parent1_idx = np.random.choice(
                self.pop_size, size=self.pop_size - 1, p=select_probs
            )
            parent2_idx = np.random.choice(
                self.pop_size, size=self.pop_size - 1, p=select_probs
            )

            children = []
            for idx in range(self.pop_size - 1):
                child = self._crossover(
                    population[parent1_idx[idx]],
                    population[parent2_idx[idx]],
                    initial_result,
                )
                if self._search_over:
                    break

                self._perturb(child, initial_result)
                children.append(child)

                # We need two `search_over` checks b/c value might change both in
                # `crossover` method and `perturb` method.
                if self._search_over:
                    break

            population = [population[0]] + children

        return population[0].result

    def check_transformation_compatibility(self, transformation):
        """
        The genetic algorithm is specifically designed for word substitutions.
        """
        return transformation_consists_of_word_swaps(transformation)

    def extra_repr_keys(self):
        return ["pop_size", "max_iters", "temp", "give_up_if_no_improvement"]


class PopulationMember:
    """
    A member of the population during the course of the genetic algorithm.
    
    Args:
        attacked_text: The ``AttackedText`` of the population member.
        num_candidates_per_word (numpy.array): A list of the number of candidate neighbors list for each word.
    """

    def __init__(self, attacked_text, num_candidates_per_word, result):
        self.attacked_text = attacked_text
        self.num_candidates_per_word = num_candidates_per_word
        self.result = result
