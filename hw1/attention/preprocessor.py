import json
import nltk
import logging
from multiprocessing import Pool
from dataset import DialogDataset
from tqdm import tqdm
import re


class Preprocessor:
    """

    Args:
        embedding_path (str): Path to the embedding to use.
    """

    def __init__(self, embedding):
        self.embedding = embedding
        self.logging = logging.getLogger(name=__name__)

    def tokenize(self, sentence):
        """ Tokenize a sentence.
        Args:
            sentence (str): One string.
        Return:
            indices (list of str): List of tokens in a sentence.
        """
        # TODO
        sentence = re.sub('https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+', '<url>', sentence)
        sentence = re.sub(r'[\w\.-]+@[\w\.-]+', '<mail>', sentence)

        # sentence = re.sub(
        #     r'''(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'".,<>?«»“”‘’]))''',
        #     "<url>", sentence)
        return nltk.word_tokenize(sentence)

    def sentence_to_indices(self, sentence):
        """ Convert sentence to its word indices.
        Args:
            sentence (str): One string.
        Return:
            indices (list of int): List of word indices.
        """
        # TODO

        result = []
        for word in self.tokenize(sentence):
            result.append(self.embedding.to_index(word))
        return result

    def collect_words(self, data_path, n_workers=4):
        with open(data_path) as f:
            data = json.load(f)

        utterances = []
        for sample in data:
            utterances += (
                    [message['utterance']
                     for message in sample['messages-so-far']]
                    + [option['utterance']
                       for option in sample['options-for-next']]
            )
        utterances = list(set(utterances))
        chunks = [
            ' '.join(utterances[i:i + len(utterances) // n_workers])
            for i in range(0, len(utterances), len(utterances) // n_workers)
        ]
        with Pool(n_workers) as pool:
            chunks = pool.map_async(self.tokenize, chunks)
            words = set(sum(chunks.get(), []))

        return words

    def get_dataset(self, data_path, n_workers=4, dataset_args={}):
        """ Load data and return Dataset objects for training and validating.

        Args:
            data_path (str): Path to the data.
            valid_ratio (float): Ratio of the data to used as valid data.
        """
        self.logging.info('loading dataset...')
        with open(data_path) as f:
            dataset = json.load(f)

        self.logging.info('preprocessing data...')

        results = [None] * n_workers
        with Pool(processes=n_workers) as pool:
            for i in range(n_workers):
                batch_start = (len(dataset) // n_workers) * i
                if i == n_workers - 1:
                    batch_end = len(dataset)
                else:
                    batch_end = (len(dataset) // n_workers) * (i + 1)

                batch = dataset[batch_start: batch_end]
                results[i] = pool.apply_async(self.preprocess_samples, [batch])

                # When debugging, you'd better not use multi-thread.
                # results[i] = self.preprocess_dataset(batch, preprocess_args)

            pool.close()
            pool.join()

        processed = []
        for result in results:
            processed += result.get()

        padding = self.embedding.to_index('</s>')
        return DialogDataset(processed, padding=padding, **dataset_args)

    def preprocess_samples(self, dataset):
        """ Worker function.

        Args:
            dataset (list of dict)
        Returns:
            list of processed dict.
        """
        processed = []
        for sample in tqdm(dataset):
            processed.append(self.preprocess_sample(sample))

        return processed

    def preprocess_sample(self, data):
        """
        Args:
            data (dict)
        Returns:
            dict
        """

        # self.embedding.to_index('<spk1>')
        # self.embedding.to_index('<spk2>')

        processed = {}
        processed['id'] = data['example-id']

        # process messages-so-far
        processed['context'] = []
        processed['speaker'] = []
        temp = []
        # print('1', data['messages-so-far'])
        # print('2', data['messages-so-far'][-3:])
        for message in data['messages-so-far'][-3:]:
            if message['speaker'] == 'participant_1':
                processed['context'].append([self.embedding.to_index('<spk1>')])
            else:
                processed['context'].append([self.embedding.to_index('<spk2>')])

            # 79
            if len(message['utterance']) > 59:
                message['utterance'] = message['utterance'][-59:]

            processed['context'].append(self.sentence_to_indices(message['utterance'].lower()))

        # print(processed['context'])
        # process options
        processed['options'] = []
        processed['option_ids'] = []

        # process correct options
        if 'options-for-correct-answers' in data:
            processed['n_corrects'] = len(data['options-for-correct-answers'])
            for option in data['options-for-correct-answers']:
                processed['options'].append(
                    self.sentence_to_indices(option['utterance'].lower())
                )
                processed['option_ids'].append(option['candidate-id'])
        else:
            processed['n_corrects'] = 0

        # process the other options
        for option in data['options-for-next']:
            if option['candidate-id'] in processed['option_ids']:
                continue

            processed['options'].append(
                self.sentence_to_indices(option['utterance'].lower())
            )
            processed['option_ids'].append(option['candidate-id'])

        return processed
