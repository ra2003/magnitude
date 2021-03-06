
from __future__ import absolute_import
import logging
#typing

#overrides

import torch

from allennlp.data.fields.production_rule_field import ProductionRuleArray
from allennlp.data.vocabulary import Vocabulary
from allennlp.modules import Attention, TextFieldEmbedder, Seq2SeqEncoder
from allennlp.nn.decoding import BeamSearch
from allennlp.nn.decoding.decoder_trainers import MaximumMarginalLikelihood
from allennlp.models.model import Model
from allennlp.models.semantic_parsing.nlvr.nlvr_decoder_state import NlvrDecoderState
from allennlp.models.semantic_parsing.nlvr.nlvr_decoder_step import NlvrDecoderStep
from allennlp.models.semantic_parsing.nlvr.nlvr_semantic_parser import NlvrSemanticParser
from allennlp.semparse.worlds import NlvrWorld

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class NlvrDirectSemanticParser(NlvrSemanticParser):
    u"""
    ``NlvrDirectSemanticParser`` is an ``NlvrSemanticParser`` that gets around the problem of lack
    of logical form annotations by maximizing the marginal likelihood of an approximate set of target
    sequences that yield the correct denotation. The main difference between this parser and
    ``NlvrCoverageSemanticParser`` is that while this parser takes the output of an offline search
    process as the set of target sequences for training, the latter performs search during training.

    Parameters
    ----------
    vocab : ``Vocabulary``
        Passed to super-class.
    sentence_embedder : ``TextFieldEmbedder``
        Passed to super-class.
    action_embedding_dim : ``int``
        Passed to super-class.
    encoder : ``Seq2SeqEncoder``
        Passed to super-class.
    attention : ``Attention``
        We compute an attention over the input question at each step of the decoder, using the
        decoder hidden state as the query.  Passed to the DecoderStep.
    decoder_beam_search : ``BeamSearch``
        Beam search used to retrieve best sequences after training.
    max_decoding_steps : ``int``
        Maximum number of steps for beam search after training.
    dropout : ``float``, optional (default=0.0)
        Probability of dropout to apply on encoder outputs, decoder outputs and predicted actions.
    """
    def __init__(self,
                 vocab            ,
                 sentence_embedder                   ,
                 action_embedding_dim     ,
                 encoder                ,
                 attention           ,
                 decoder_beam_search            ,
                 max_decoding_steps     ,
                 dropout        = 0.0)        :
        super(NlvrDirectSemanticParser, self).__init__(vocab=vocab,
                                                       sentence_embedder=sentence_embedder,
                                                       action_embedding_dim=action_embedding_dim,
                                                       encoder=encoder,
                                                       dropout=dropout)
        self._decoder_trainer = MaximumMarginalLikelihood()
        self._decoder_step = NlvrDecoderStep(encoder_output_dim=self._encoder.get_output_dim(),
                                             action_embedding_dim=action_embedding_dim,
                                             input_attention=attention,
                                             dropout=dropout)
        self._decoder_beam_search = decoder_beam_search
        self._max_decoding_steps = max_decoding_steps
        self._action_padding_index = -1

    #overrides
    def forward(self,  # type: ignore
                sentence                             ,
                worlds                       ,
                actions                                 ,
                identifier            = None,
                target_action_sequences                   = None,
                labels                   = None)                           :
        # pylint: disable=arguments-differ
        u"""
        Decoder logic for producing type constrained target sequences, trained to maximize marginal
        likelihod over a set of approximate logical forms.
        """
        batch_size = len(worlds)
        action_embeddings, action_indices = self._embed_actions(actions)

        initial_rnn_state = self._get_initial_rnn_state(sentence)
        initial_score_list = [iter(list(sentence.values())).next().new_zeros(1, dtype=torch.float)
                              for i in range(batch_size)]
        label_strings = self._get_label_strings(labels) if labels is not None else None
        # TODO (pradeep): Assuming all worlds give the same set of valid actions.
        initial_grammar_state = [self._create_grammar_state(worlds[i][0], actions[i]) for i in
                                 range(batch_size)]
        worlds_list = [worlds[i] for i in range(batch_size)]

        initial_state = NlvrDecoderState(batch_indices=range(batch_size),
                                         action_history=[[] for _ in range(batch_size)],
                                         score=initial_score_list,
                                         rnn_state=initial_rnn_state,
                                         grammar_state=initial_grammar_state,
                                         action_embeddings=action_embeddings,
                                         action_indices=action_indices,
                                         possible_actions=actions,
                                         worlds=worlds_list,
                                         label_strings=label_strings)

        if target_action_sequences is not None:
            # Remove the trailing dimension (from ListField[ListField[IndexField]]).
            target_action_sequences = target_action_sequences.squeeze(-1)
            target_mask = target_action_sequences != self._action_padding_index
        else:
            target_mask = None

        outputs                          = {}
        if identifier is not None:
            outputs[u"identifier"] = identifier
        if target_action_sequences is not None:
            outputs = self._decoder_trainer.decode(initial_state,
                                                   self._decoder_step,
                                                   (target_action_sequences, target_mask))
        best_final_states = self._decoder_beam_search.search(self._max_decoding_steps,
                                                             initial_state,
                                                             self._decoder_step,
                                                             keep_final_unfinished_states=False)
        best_action_sequences                             = {}
        for i in range(batch_size):
            # Decoding may not have terminated with any completed logical forms, if `num_steps`
            # isn't long enough (or if the model is not trained enough and gets into an
            # infinite action loop).
            if i in best_final_states:
                best_action_indices = [best_final_states[i][0].action_history[0]]
                best_action_sequences[i] = best_action_indices
        batch_action_strings = self._get_action_strings(actions, best_action_sequences)
        batch_denotations = self._get_denotations(batch_action_strings, worlds)
        if target_action_sequences is not None:
            self._update_metrics(action_strings=batch_action_strings,
                                 worlds=worlds,
                                 label_strings=label_strings)
        else:
            outputs[u"best_action_strings"] = batch_action_strings
            outputs[u"denotations"] = batch_denotations
        return outputs

    def _update_metrics(self,
                        action_strings                       ,
                        worlds                       ,
                        label_strings                 )        :
        # TODO(pradeep): Move this to the base class.
        # TODO(pradeep): Using only the best decoded sequence. Define metrics for top-k sequences?
        batch_size = len(worlds)
        for i in range(batch_size):
            instance_action_strings = action_strings[i]
            sequence_is_correct = [False]
            if instance_action_strings:
                instance_label_strings = label_strings[i]
                instance_worlds = worlds[i]
                # Taking only the best sequence.
                sequence_is_correct = self._check_denotation(instance_action_strings[0],
                                                             instance_label_strings,
                                                             instance_worlds)
            for correct_in_world in sequence_is_correct:
                self._denotation_accuracy(1 if correct_in_world else 0)
            self._consistency(1 if all(sequence_is_correct) else 0)

    #overrides
    def get_metrics(self, reset       = False)                    :
        return {
                u'denotation_accuracy': self._denotation_accuracy.get_metric(reset),
                u'consistency': self._consistency.get_metric(reset)
        }

NlvrDirectSemanticParser = Model.register(u"nlvr_direct_parser")(NlvrDirectSemanticParser)
