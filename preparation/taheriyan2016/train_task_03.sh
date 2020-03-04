#!/bin/bash
start_all=$(date +%s.%N)
for i in $(ls data/taheriyan2016/task_03/learning_datasets/)
do
    printf "\n"
    echo "Run script for" ${i}
    start=$(date +%s.%N)
    python src/link_prediction/link_predict.py \
    --directory data/taheriyan2016/task_03/learning_datasets/${i}/ \
    --train data/taheriyan2016/task_03/learning_datasets/${i}/train.nt \
    --valid data/taheriyan2016/task_03/learning_datasets/${i}/valid.nt \
    --test data/taheriyan2016/task_03/learning_datasets/${i}/test.nt \
    --score ${i} \
    --parser TAH \
    --gpu 0 \
    --graph-batch-size 20000 \
    --n-hidden 200
    # --evaluate-every 10 \
    # --n-epochs 20
    duration=$(echo "$(date +%s.%N) - $start" | bc)
    duration_until_now=$(echo "$(date +%s.%N) - $start_all" | bc)
    execution_time=`printf "%d seconds" $duration`
    execution_time_until_now=`printf "%d seconds" $duration_until_now`
    printf "\n"
    echo "Time for training with background of" ${i} "is" ${execution_time}
    echo "Time for training until now is" ${execution_time_until_now}
done
duration_all=$(echo "$(date +%s.%N) - $start_all" | bc)
execution_time_all=`printf "%d seconds" $duration_all`
printf "\n"
echo "Total time for training data in Task 03 is" ${execution_time_all}
