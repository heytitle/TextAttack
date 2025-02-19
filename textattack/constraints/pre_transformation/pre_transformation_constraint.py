from abc import ABC, abstractmethod

from textattack.constraints import Constraint


class PreTransformationConstraint(Constraint, ABC):
    """ 
    An abstract class that represents constraints which are applied before
    the transformation. These restrict which words are allowed to be modified
    during the transformation. For example, we might not allow stopwords to be
    modified.
    """

    def __call__(self, current_text, transformation):
        """ 
        Returns the word indices in ``current_text`` which are able to be modified. 
        First checks compatibility with ``transformation`` then calls ``_get_modifiable_indices``\.

        Args:
            current_text: The ``AttackedText`` input to consider.
            transformation: The ``Transformation`` which will be applied.
        """
        if not self.check_compatibility(transformation):
            return set(range(len(current_text.words)))
        return self._get_modifiable_indices(current_text)

    @abstractmethod
    def _get_modifiable_indices(current_text):
        """
        Returns the word indices in ``current_text`` which are able to be modified. 
        Must be overridden by specific pre-transformation constraints.

        Args:
            current_text: The ``AttackedText`` input to consider.
        """
        raise NotImplementedError()

    def _check_constraint(self):
        raise RuntimeError(
            "PreTransformationConstraints do not support `_check_constraint()`."
        )
