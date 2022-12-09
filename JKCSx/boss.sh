#!/bin/bash
#sh manager.sh INPUT
start=`date +%s`

# locate TOOLS path
scriptpath="$( cd "$(dirname "$0")" ; pwd -P )"
toolspath="$scriptpath/../TOOLS"
source ~/.JKCSusersetup.txt

input=$1
if [ -z "$input" ];then echo Missing input;exit;fi
if [ ! -e "$input" ];then echo Input file does not exist;exit;fi
start_line=$2 
if [ -z "$start_line" ];then start_line=1;fi
line_num=$start_line

function submitME {
  #sbatch -p q24,q36,q40,q48 --time 30:00 -n 1 -J "BOSS" JKsend 
  submit_BOSS sh $scriptpath/boss.sh $input $line_num "SUB"
}
if [ -z "$3" ]; then submitME; exit; fi

max_walltime=`echo 24*60*60 | bc` #in seconds
max_SingleCommandExecutionTime=`echo 60*60 | bc` #in seconds
walltime_cutoff=`echo $max_walltime-$max_SingleCommandExecutionTime | bc`
max_Managers=4

function check_if_all_finished {
  test=0
  while [ $test -eq 0 ]
  do
    numOfMangs=`squeue -u $USER | awk '{print $3}' | grep manager | wc -l`
    if [ $numOfMangs -le $max_Managers ]
    then
      test=1
    fi
    end=`date +%s`;runtime=$((end-start))
    if [ $runtime -gt $walltime_cutoff ]
    then 
      submitME
      exit
    else 
      sleep 5
    fi
  done
  return
}

lines=`wc -l $input | awk '{print $1}'`
original_directory=$PWD
for line_num in `seq $start_line $lines`
do
  cd $original_directory
  check_if_all_finished
  line=`head -n $line_num $input | tail -n 1`
  echo $line > commandsNOW.txt
  echo $line >> commandsSENT.txt
  job=$(/bin/bash commandsNOW.txt)
  if [ ! -z "$job" ] ;then echo $job;fi
  echo $job | awk '{print $4}'  >> .jobs.txt
  rm commandsNOW.txt
done
