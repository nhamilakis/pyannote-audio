from typing import List, Dict, Tuple
import yaml
import ipdb
import bisect
import numpy as np
#from pyannote.audio.features import FeatureExtraction
from pyannote.audio.features.utils import get_audio_duration
from pyannote.core import SlidingWindow, Segment
from pyannote.core.utils.helper import get_class_by_name
from pyannote.core.utils.numpy import one_hot_encoding
from pyannote.database import get_protocol, FileFinder
from pyannote.database.util import LabelMapper
from pyannote.database import get_unique_identifier

# window parameters are in seconds
#window = SlidingWindow(duration=0.01, step=0.01, start=0.0)

class AnnotatedFile:
    # window parameters are in seconds
    #window = SlidingWindow(duration=0.01, step=0.01, start=0.0)
    #sample_duration = 2  # in seconds as well

    def __init__(self, annot_data: Dict, all_labels: List[str], duration: float):
        """Annot data is generated by the pyannote db file"""
        self.annot_data = annot_data
        self.labels = all_labels
        self.total_frames_nb = None
        self.window = self.annot_data['y'].sliding_window
        #self.sample_duration = self.annot_data['duration']
        self.sample_duration = duration
        # one_hot encoding returns array of size N x L, N is the
        # time, L is the number of labels
        feats = self.annot_data['y'].data
        #feats, _ = one_hot_encoding(self.annot_data["annotation"],
        #                            self.annot_data["annotated"],
        #                            self.window,
        #                            labels=self.labels)
        self.positive_one_hot = feats.swapaxes(0, 1).astype(np.bool)
        self.negative_one_hot = np.invert(self.positive_one_hot)
        # setting the borders of the one hot encoding (corresponding to the borders of the sound file)
        # to False to prevent sampling between two file when they are concatenated
        samples_nb = self.window.samples(int(self.sample_duration / 2)) + 1
        self.positive_one_hot[:, :samples_nb] = False
        self.positive_one_hot[:, -samples_nb:] = False
        self.negative_one_hot[:, :samples_nb] = False
        self.negative_one_hot[:, -samples_nb:] = False
        self.total_frames_nb = self.positive_one_hot.shape[1]
        #print('passed in annotatedFile')

    @property
    def uri(self):
        return  get_unique_identifier(self.annot_data['current_file'])

    def get_frames_around_idx(self, idx):
        #, sample_type="pos"):
        #assert sample_type in ("pos", "neg")
        nb_frames = self.window.durationToSamples(int(self.sample_duration / 2))

        lower_bound = idx - nb_frames
        upper_bound = idx + nb_frames
        return lower_bound, upper_bound

class Domain:
    """treat files that have the same domain"""

    def __init__(self, domain_name, annotated_files, frame_info, all_labels):
        self.domain_name = domain_name
        #print(domain_name)
        self.window = frame_info
        self.all_labels = all_labels
        self.annotated_files = annotated_files

        # get labels 1 hot
        positive_one_hot = np.concatenate([file.positive_one_hot for file in self.annotated_files], axis=1)
        negative_one_hot = np.concatenate([file.negative_one_hot for file in self.annotated_files], axis=1)

        # building a vector of the range of indexes that correspond to each file
        # in the concatenated file
        indexes_ranges = [0]
        for i, file in enumerate(self.annotated_files):
            indexes_ranges.append(indexes_ranges[i] + file.total_frames_nb)
        self.indexes_ranges = np.array(indexes_ranges)

        self.total_size = positive_one_hot.shape[0]
        self.positive_freqs = positive_one_hot.sum(1)
        self.negative_freqs = negative_one_hot.sum(1)
        self.positive_indexes = {label: np.argwhere(positive_one_hot[i] == True).flatten()
                                 for i, label in enumerate(self.all_labels)}
        self.negative_indexes = {label: np.argwhere(negative_one_hot[i] == True).flatten()
                                 for i, label in enumerate(self.all_labels)}

    def _get_file(self, frame_index: int):
        """ from index in concatenated 1hot, get corresponding input file
            and segment position in that file"""
        file_idx = bisect.bisect_left(self.indexes_ranges, frame_index) - 1
        frame_file_idx = frame_index - self.indexes_ranges[file_idx]
        onset, offset = self.annotated_files[file_idx].get_frames_around_idx(frame_file_idx)
        file_uri = self.annotated_files[file_idx].uri
        return self.window.samplesToDuration(onset), self.window.samplesToDuration(offset), file_uri

    def _sample_positive(self, label: str):
        """ return sampled index in concatenated 1hot"""
        return np.random.choice(self.positive_indexes[label])

    def _sample_negative(self, label: str):
        return np.random.choice(self.negative_indexes[label])

    def sample_segment(self, label: str, positive: bool): 
        """Given a label and depending on $positive, 
           return a positive or negative example for label
           Returns
           -------
           onset:  float
                   onset of sampled example
           offset: float
                   offset of sampled example
           uri:    str
                   name of the file in which segment was sampled
        """
        if positive:
            index = self._sample_positive(label)
        else:
            index = self._sample_negative(label)
        return self._get_file(index)


class Domain_set:
    """A set of domains"""


    def __init__(self, label_mapping, data_, frame_info, all_labels: List[str], duration: float):

        #preprocessors = {"annotation": LabelMapper(mapping=label_mapping, keep_missing=False)}

        self.all_labels = all_labels

        #TODO FIX
        self.min_freq = True


        # get subset files
        self.data_ = data_

        file_set = [data_[uri] for uri in data_]
        #self.annotated_files = [AnnotatedFile(annot, all_labels) for annot in files]

        self.domain_names = {fin['current_file']['domain'] for fin in file_set}
        #ipdb.set_trace()
           
        # self.data_ = {fin['uri']: fin for fin in file_set}
        #ipdb.set_trace()
        # get label frequencies per domain
        self.domain_set = [] 
        self.domain_labelFreqs = np.zeros((len(all_labels), len(self.domain_names)))
        for i, domain_name in enumerate(self.domain_names):
            domain_annotated_files = [AnnotatedFile(fin, all_labels, duration) for fin in file_set
                                      if fin['current_file']['domain'] == domain_name]
            domain = Domain(domain_name, domain_annotated_files, frame_info, all_labels)
            self.domain_set.append(domain)
            
            # compute frequencies for each label
            for j, label in enumerate(all_labels):
                if self.min_freq:
                    self.domain_labelFreqs[j, i] = min(domain.positive_freqs[j],
                                                       domain.negative_freqs[j])
                else:
                    self.domain_labelFreqs[j, i] = domain.prositive_freqs[j]

 
        # get frequencies to sum to 1 across domains for each label
        self.domain_labelFreqs = np.nan_to_num(self._rownorm(self.domain_labelFreqs))

        # check labels and throw exception when one label never occurs in any domain
        #self.check_labelFreqs(self.domain_labelFreqs)

        ## Add duration to "current_files" in data, needed for Feature Extraction
        #for uri in self.data_:
        #    # taken from labeling/tasks/base.py in pyannote audio
        #    segments = [s for s in self.data_[uri]['annotated']
        #                if s.duration > self.duration]
        #    duration = sum(s.duration for s in segments)
        #    self.data_[uri]['duration'] = duration
        # get file info

        # get labels one hot encodings
        self.label_indexes = {label: i for i, label in enumerate(self.all_labels)}
        self.domain_indexes = {domain: i for i, domain in enumerate(self.domain_set)}

    @staticmethod
    def _rownorm(array: np.ndarray):
        return array / np.linalg.norm(array, axis=1, ord=1, keepdims=True)

    def sample_domain(self, label: str):
        """ Sample the corpus using the current label frenquencies across corpora
            as a probability distribution for the sampling.
        """
        #print( self.domain_labelFreqs[self.label_indexes[label]])
        sampled_domain = np.random.choice(self.domain_set, p = self.domain_labelFreqs[self.label_indexes[label]])

        return sampled_domain, self.domain_indexes[sampled_domain]

    #def check_labelFreqs(self, domain_labelFreqs):
    #    for lab in domain_labelFreqs.shape[0]:
    #        assert np.sum(domain_labelFreqs[lab,:]) == 1, "Please check requested labels. Label frequency for {lab} across domains should be 1 but is {val}".format(lab=self.all_labels[lab],
    #                                                            val=np.sum(domain_labelFreqs[lab,:]))
    #        

